package engine

import (
	"context"
	"errors"
	"fmt"
	"net/netip"
	"reflect"
	"strings"
	"sync"
	"time"
	"unsafe"

	corehost "github.com/easytier/easytier/easytier-go"
	"github.com/metacubex/mihomo/adapter/outbound"
	C "github.com/metacubex/mihomo/constant"
	ZT "github.com/metacubex/zerotier-go"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// How the mesh outbounds stand (GetMeshStatus, LoginMesh; docs/mesh.md §10.5). mihomo
// v1.19.33 keeps the tsnet server, the ZeroTier runtime and the EasyTier instance in
// unexported fields and offers no way to ask them, so the probes read those fields with
// reflect, under the outbounds' own locks. Each field is checked by name and type: a
// mihomo that moved one makes the probe fail with an error rather than read the wrong
// memory, and the tests that check every field against the pinned version fail first.

// meshProbeTimeout bounds what one outbound may take to answer.
const meshProbeTimeout = 2 * time.Second

// meshLoginTimeout bounds the wait for a login URL.
const meshLoginTimeout = 20 * time.Second

// MeshStatus describes the running configuration's mesh outbounds, in its order.
func (e *Engine) MeshStatus(ctx context.Context) ([]*controlpb.MeshStatus, error) {
	if !e.running() {
		return nil, ErrNotRunning
	}
	outbounds := e.meshOutbounds()
	statuses := make([]*controlpb.MeshStatus, len(outbounds))
	var wait sync.WaitGroup
	for i, mesh := range outbounds {
		wait.Go(func() {
			probeCtx, cancel := context.WithTimeout(ctx, meshProbeTimeout)
			defer cancel()
			statuses[i] = mesh.status(probeCtx)
		})
	}
	wait.Wait()
	return statuses, nil
}

// LoginMesh gives the URL that logs the outbound in: a Tailscale login it starts, or a
// ZeroTier network's single sign-on.
func (e *Engine) LoginMesh(ctx context.Context, name string) (string, error) {
	if !e.running() {
		return "", ErrNotRunning
	}
	mesh := e.meshOutbound(name)
	if mesh == nil {
		return "", fmt.Errorf("%w: the running configuration has no mesh outbound %q", ErrInvalidArgument, name)
	}
	ctx, cancel := context.WithTimeout(ctx, meshLoginTimeout)
	defer cancel()
	switch concrete := unwrapOutbound(mesh.adapter).(type) {
	case *outbound.Tailscale:
		return tailscaleLogin(ctx, concrete)
	case *outbound.ZeroTier:
		state, err := zeroTierState(concrete)
		if err != nil {
			return "", err
		}
		if state.authURL == "" {
			return "", fmt.Errorf("%w: %s asks for no login", ErrInvalidArgument, name)
		}
		return state.authURL, nil
	}
	return "", fmt.Errorf("%w: %s has no login", ErrInvalidArgument, name)
}

func (m *meshOutbound) status(ctx context.Context) *controlpb.MeshStatus {
	status := &controlpb.MeshStatus{
		Name:  m.name,
		Id:    m.id,
		Type:  m.adapter.Type().String(),
		State: controlpb.MeshState_MESH_STATE_STARTING,
	}
	var err error
	switch concrete := unwrapOutbound(m.adapter).(type) {
	case *outbound.Tailscale:
		err = tailscaleStatus(ctx, concrete, status)
	case *outbound.ZeroTier:
		err = zeroTierStatus(concrete, status)
	case *outbound.EasyTier:
		err = easyTierStatus(ctx, concrete, status)
	default:
		err = fmt.Errorf("%T is no mesh outbound", concrete)
	}
	if err != nil {
		status.State, status.Error = controlpb.MeshState_MESH_STATE_FAILED, err.Error()
	} else if status.State == controlpb.MeshState_MESH_STATE_STARTING {
		// Still starting, unless the start failed: ZeroTier's runtime and EasyTier's
		// instance stay empty then, and only the start said why.
		if startErr := m.lastStartErr(); startErr != nil {
			status.State, status.Error = controlpb.MeshState_MESH_STATE_FAILED, startErr.Error()
		}
	}
	return status
}

// unwrapOutbound is the outbound inside mihomo's wrapper, which closes it once collected.
func unwrapOutbound(wrapped C.ProxyAdapter) C.ProxyAdapter {
	value := reflect.ValueOf(wrapped)
	if value.Kind() != reflect.Pointer || value.Elem().Kind() != reflect.Struct {
		return wrapped
	}
	inner := value.Elem().FieldByName("ProxyAdapter")
	if !inner.IsValid() || inner.IsNil() {
		return wrapped
	}
	if concrete, ok := inner.Interface().(C.ProxyAdapter); ok {
		return concrete
	}
	return wrapped
}

// fieldValue is the field name of the struct that pointer points to, as a value that may
// be read whether the field is exported or not.
func fieldValue(pointer reflect.Value, name string) (reflect.Value, error) {
	if pointer.Kind() != reflect.Pointer || pointer.IsNil() || pointer.Elem().Kind() != reflect.Struct {
		return reflect.Value{}, fmt.Errorf("%s is no pointer to a struct", pointer.Type())
	}
	field := pointer.Elem().FieldByName(name)
	if !field.IsValid() {
		return reflect.Value{}, fmt.Errorf("%s has no field %s; is mihomo not v1.19.33?", pointer.Type(), name)
	}
	return reflect.NewAt(field.Type(), unsafe.Pointer(field.UnsafeAddr())).Elem(), nil
}

// fieldOf is a pointer to the field name of *owner, which must have type T.
func fieldOf[T any](owner any, name string) (*T, error) {
	field, err := fieldValue(reflect.ValueOf(owner), name)
	if err != nil {
		return nil, err
	}
	if want := reflect.TypeFor[T](); field.Type() != want {
		return nil, fmt.Errorf("%T.%s is %s, not %s; is mihomo not v1.19.33?", owner, name, field.Type(), want)
	}
	return field.Addr().Interface().(*T), nil
}

// hostPrefix writes an address of the device as a one-address prefix.
func hostPrefix(address netip.Addr) string {
	return netip.PrefixFrom(address, address.BitLen()).String()
}

// zeroTierSnapshot is what the ZeroTier outbound keeps under its stateMu.
type zeroTierSnapshot struct {
	node       *ZT.Node // nil until it starts
	config     ZT.NetworkConfigData
	joined     bool // the network's stack is up
	networkErr error
	authURL    string
}

// zeroTierState copies the outbound's network state. stateMu is never held while calling
// into the node, as zerotier.go's lock rules say, so the node is asked afterwards.
func zeroTierState(z *outbound.ZeroTier) (zeroTierSnapshot, error) {
	var state zeroTierSnapshot
	mu, err := fieldOf[sync.RWMutex](z, "stateMu")
	if err != nil {
		return state, err
	}
	pointer := reflect.ValueOf(z)
	runtime, err := fieldValue(pointer, "runtime")
	if err != nil {
		return state, err
	}
	device, err := fieldValue(pointer, "tunDevice")
	if err != nil {
		return state, err
	}
	config, err := fieldOf[ZT.NetworkConfigData](z, "config")
	if err != nil {
		return state, err
	}
	networkErr, err := fieldOf[error](z, "networkErr")
	if err != nil {
		return state, err
	}
	authURL, err := fieldOf[string](z, "authURL")
	if err != nil {
		return state, err
	}
	mu.RLock()
	defer mu.RUnlock()
	if !runtime.IsNil() {
		node, err := fieldValue(runtime, "node")
		if err != nil {
			return state, err
		}
		state.node, _ = node.Interface().(*ZT.Node)
	}
	state.config = *config
	state.joined = !device.IsNil()
	state.networkErr = *networkErr
	state.authURL = *authURL
	return state, nil
}

func zeroTierStatus(z *outbound.ZeroTier, status *controlpb.MeshStatus) error {
	state, err := zeroTierState(z)
	if err != nil {
		return err
	}
	if state.node != nil {
		describeZeroTier(state, state.node.Address().String(), state.node.Peers, status)
	}
	return nil
}

// describeZeroTier fills in the status of a started node.
func describeZeroTier(state zeroTierSnapshot, nodeID string, peers func() []ZT.PeerStatus, status *controlpb.MeshStatus) {
	status.NodeId = nodeID
	switch {
	case state.authURL != "":
		status.State, status.LoginUrl = controlpb.MeshState_MESH_STATE_NEEDS_LOGIN, state.authURL
	case state.networkErr != nil && strings.Contains(state.networkErr.Error(), "access denied"):
		// The controller has not authorized the node yet.
		status.State = controlpb.MeshState_MESH_STATE_WAITING
	case state.networkErr != nil:
		status.State, status.Error = controlpb.MeshState_MESH_STATE_FAILED, state.networkErr.Error()
	case state.joined:
		status.State = controlpb.MeshState_MESH_STATE_ONLINE
		status.NetworkName = state.config.Name
		for _, prefix := range state.config.Assigned {
			status.Addresses = append(status.Addresses, prefix.String())
		}
		for _, peer := range peers() {
			if peer.Role == ZT.PeerRoleLeaf && len(peer.Paths) > 0 {
				status.PeerCount++
			}
		}
	}
}

func easyTierInstance(e *outbound.EasyTier) (*corehost.Instance, error) {
	mu, err := fieldOf[sync.Mutex](e, "mu")
	if err != nil {
		return nil, err
	}
	instance, err := fieldOf[*corehost.Instance](e, "instance")
	if err != nil {
		return nil, err
	}
	mu.Lock()
	defer mu.Unlock()
	return *instance, nil
}

func easyTierStatus(ctx context.Context, e *outbound.EasyTier, status *controlpb.MeshStatus) error {
	instance, err := easyTierInstance(e)
	if err != nil || instance == nil || instance.State() != corehost.StateRunning {
		return err
	}
	info, err := instance.ShowNodeInfo(ctx)
	if err != nil {
		// The instance may be restarting; the next look tells.
		return nil
	}
	address := easyTierAddress(info.GetIpv4Addr())
	if address == "" {
		return nil // waiting for the network to assign one
	}
	status.State = controlpb.MeshState_MESH_STATE_ONLINE
	status.Addresses = []string{address}
	if routes, err := instance.ListRoute(ctx); err == nil {
		for _, route := range routes {
			if route != nil {
				status.PeerCount++
			}
		}
	}
	return nil
}

// easyTierAddress reads EasyTier's "10.144.144.1/24", or a bare address, as a prefix.
func easyTierAddress(text string) string {
	if prefix, err := netip.ParsePrefix(text); err == nil {
		return prefix.String()
	}
	if address, err := netip.ParseAddr(text); err == nil {
		return hostPrefix(address)
	}
	return ""
}

var errNoTailscale = errors.New("this core is built without Tailscale")
