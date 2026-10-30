package control

import (
	"context"
	"fmt"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/warp"
)

// warpEngine registers with fixed answers and keeps what it was asked.
type warpEngine struct {
	unlinked
	profile *engine.Profile
	via     string
	request warp.Request
	err     error
}

func (e *warpEngine) Validate(profile engine.Profile) (*controlpb.CompositionReport, error) {
	e.profile = &profile
	return &controlpb.CompositionReport{}, nil
}

func (e *warpEngine) RegisterWarp(_ context.Context, via string, request warp.Request) (*controlpb.WarpAccount, error) {
	e.via, e.request = via, request
	if e.err != nil {
		return nil, e.err
	}
	return &controlpb.WarpAccount{DeviceId: "d", Token: "t", Protocol: request.Protocol}, nil
}

func registerWarp(id uint64, register *controlpb.RegisterWarpRequest) *controlpb.Request {
	return request(id, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_RegisterWarp{RegisterWarp: register}
	})
}

func TestRegisterWarp(t *testing.T) {
	fake := &warpEngine{}
	dispatcher := New(Options{Engine: fake})
	response := call(t, dispatcher, registerWarp(1, &controlpb.RegisterWarpRequest{
		Via:      "节点选择",
		Protocol: controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD,
		DeviceId: "d",
		Token:    "t",
	}))
	if account := response.GetRegisterWarp().GetAccount(); account.GetDeviceId() != "d" ||
		account.GetProtocol() != controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD {
		t.Fatalf("register_warp = %v", response)
	}
	if fake.via != "节点选择" || fake.request != (warp.Request{
		Protocol: controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD, DeviceID: "d", Token: "t",
	}) {
		t.Fatalf("asked for %+v via %q", fake.request, fake.via)
	}

	// Cloudflare's refusals are operation_failed with its text, worth another try.
	fake.err = fmt.Errorf("%w (Cloudflare: 429 error code: 1015)", warp.ErrRateLimited)
	failed := call(t, dispatcher, registerWarp(2, &controlpb.RegisterWarpRequest{Via: "节点选择"}))
	expectError(t, failed, 2, CodeOperationFailed)
	if failed.GetError().GetMessage() != fake.err.Error() || !failed.GetError().GetRetryable() {
		t.Fatalf("error = %v", failed.GetError())
	}

	for id, malformed := range map[uint64]*controlpb.RegisterWarpRequest{
		3: {},
		4: {Via: "节点选择", Protocol: 9},
		5: {Via: "节点选择", DeviceId: "d"},
		6: {Via: "节点选择", Token: "t"},
	} {
		expectError(t, call(t, dispatcher, registerWarp(id, malformed)), id, CodeInvalidRequest)
	}
}

// The device rides in every composition the UI sends.
func TestCompositionCarriesTheWarpDevice(t *testing.T) {
	fake := &warpEngine{}
	dispatcher := New(Options{Engine: fake, HostMethods: true})
	account := &controlpb.WarpAccount{DeviceId: "d", Token: "t", PrivateKey: "k", Endpoint: "162.159.198.1:443"}
	composition := compositionOf(`{"version": 1}`)
	composition.Egress = &controlpb.Egress{Warp: account}
	call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{
			Source: &controlpb.ValidateRequest_Composition{Composition: composition},
		}}
	}))
	if fake.profile == nil || !proto.Equal(fake.profile.Composition.Warp, account) {
		t.Fatalf("profile = %+v", fake.profile)
	}
}

// Registration needs the running configuration's groups, on every host.
func TestRegisterWarpNeedsARunningCore(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: core, HostMethods: host})
		stopped := call(t, dispatcher, registerWarp(1, &controlpb.RegisterWarpRequest{Via: "节点选择"}))
		expectError(t, stopped, 1, CodeNotRunning)
		dispatcher.Close()
	}
}
