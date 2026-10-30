//go:build with_gvisor && !no_tailscale

package engine

import (
	"context"
	"errors"
	"time"

	"github.com/metacubex/mihomo/adapter/outbound"
	"github.com/metacubex/tailscale/client/local"
	"github.com/metacubex/tailscale/ipn"
	"github.com/metacubex/tailscale/ipn/ipnstate"
	"github.com/metacubex/tailscale/tsnet"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// tailscaleClient is the outbound's tsnet LocalAPI once its backend has come up. Before
// that the outbound may not have started its server, and asking it then would start a
// second one that the outbound never closes; ready reports whether it has.
func tailscaleClient(t *outbound.Tailscale) (client *local.Client, ready bool, err error) {
	initialized, err := fieldOf[chan struct{}](t, "backendInitCh")
	if err != nil {
		return nil, false, err
	}
	select {
	case <-*initialized:
	default:
		return nil, false, nil
	}
	// Both are set before backendInitCh closes and never after.
	initErr, err := fieldOf[error](t, "backendInitErr")
	if err != nil {
		return nil, false, err
	}
	if *initErr != nil {
		return nil, true, *initErr
	}
	server, err := fieldOf[*tsnet.Server](t, "server")
	if err != nil {
		return nil, false, err
	}
	client, err = (*server).LocalClient()
	return client, true, err
}

func tailscaleStatus(ctx context.Context, t *outbound.Tailscale, status *controlpb.MeshStatus) error {
	client, ready, err := tailscaleClient(t)
	if err != nil || !ready {
		return err
	}
	st, err := client.Status(ctx)
	if err != nil {
		return err
	}
	describeTailscale(st, status)
	return nil
}

// describeTailscale fills in the status from tsnet's.
func describeTailscale(st *ipnstate.Status, status *controlpb.MeshStatus) {
	switch st.BackendState {
	case ipn.NeedsLogin.String():
		status.State, status.LoginUrl = controlpb.MeshState_MESH_STATE_NEEDS_LOGIN, st.AuthURL
	case ipn.NeedsMachineAuth.String():
		status.State = controlpb.MeshState_MESH_STATE_WAITING
	case ipn.Running.String():
		status.State = controlpb.MeshState_MESH_STATE_ONLINE
	case ipn.Stopped.String():
		status.State, status.Error = controlpb.MeshState_MESH_STATE_FAILED, "Tailscale stopped"
	}
	for _, address := range st.TailscaleIPs {
		status.Addresses = append(status.Addresses, hostPrefix(address))
	}
	for _, peer := range st.Peer {
		if peer.Online {
			status.PeerCount++
		}
	}
	if st.CurrentTailnet != nil {
		status.NetworkName = st.CurrentTailnet.Name
	}
}

// tailscaleLogin starts an interactive login, which also switches a logged-in device to
// another account, and waits for its URL.
func tailscaleLogin(ctx context.Context, t *outbound.Tailscale) (string, error) {
	var client *local.Client
	for client == nil {
		var ready bool
		var err error
		if client, ready, err = tailscaleClient(t); err != nil {
			return "", err
		}
		if !ready {
			select {
			case <-ctx.Done():
				return "", errors.New("Tailscale has not started yet")
			case <-time.After(250 * time.Millisecond):
			}
		}
	}
	if err := client.StartLoginInteractive(ctx); err != nil {
		return "", err
	}
	for {
		st, err := client.StatusWithoutPeers(ctx)
		if err != nil {
			return "", err
		}
		if st.AuthURL != "" {
			return st.AuthURL, nil
		}
		select {
		case <-ctx.Done():
			return "", errors.New("Tailscale gave no login URL in time")
		case <-time.After(250 * time.Millisecond):
		}
	}
}
