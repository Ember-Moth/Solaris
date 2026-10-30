package control

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// vpnGateEngine lists fixed servers and keeps what it was asked.
type vpnGateEngine struct {
	unlinked
	profile *admission.Profile
	via     string
	err     error
}

func (e *vpnGateEngine) Validate(profile admission.Profile) (*controlpb.CompositionReport, error) {
	e.profile = &profile
	return &controlpb.CompositionReport{}, nil
}

func (e *vpnGateEngine) ListVpnGate(_ context.Context, via string) ([]*controlpb.VpnGateServer, error) {
	e.via = via
	if e.err != nil {
		return nil, e.err
	}
	return []*controlpb.VpnGateServer{{HostName: "vpn1", Country: "JP"}}, nil
}

func listVpnGate(id uint64, via string) *controlpb.Request {
	return request(id, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_ListVpnGate{ListVpnGate: &controlpb.ListVpnGateRequest{Via: via}}
	})
}

func TestListVpnGate(t *testing.T) {
	fake := &vpnGateEngine{}
	dispatcher := New(Options{Engine: fake})
	response := call(t, dispatcher, listVpnGate(1, "节点选择"))
	if servers := response.GetListVpnGate().GetServers(); len(servers) != 1 || servers[0].GetHostName() != "vpn1" || fake.via != "节点选择" {
		t.Fatalf("list_vpn_gate = %v via %q", response, fake.via)
	}

	// VPN Gate's refusals are operation_failed with its text, worth another try.
	fake.err = errors.New("VPN Gate answered 503 Service Unavailable: busy")
	failed := call(t, dispatcher, listVpnGate(2, "节点选择"))
	expectError(t, failed, 2, CodeOperationFailed)
	if failed.GetError().GetMessage() != fake.err.Error() || !failed.GetError().GetRetryable() {
		t.Fatalf("error = %v", failed.GetError())
	}
	expectError(t, call(t, dispatcher, listVpnGate(3, "")), 3, CodeInvalidRequest)
}

// The picked servers ride in every composition the UI sends.
func TestCompositionCarriesTheVpnGateServers(t *testing.T) {
	fake := &vpnGateEngine{}
	dispatcher := New(Options{Engine: fake, HostMethods: true})
	servers := []*controlpb.VpnGateServer{{HostName: "vpn1", Country: "JP", Openvpn: &controlpb.OpenVpnOptions{Server: "203.0.113.1", Port: 443}}}
	composition := compositionOf(`{"version": 1}`)
	composition.Egress = &controlpb.Egress{VpnGate: servers}
	call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{
			Source: &controlpb.ValidateRequest_Composition{Composition: composition},
		}}
	}))
	if fake.profile == nil || len(fake.profile.Composition.VpnGate) != 1 || !proto.Equal(fake.profile.Composition.VpnGate[0], servers[0]) {
		t.Fatalf("profile = %+v", fake.profile)
	}
}

// The list goes out through the running configuration's groups, on every host.
func TestListVpnGateNeedsARunningCore(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: core, HostMethods: host})
		expectError(t, call(t, dispatcher, listVpnGate(1, "节点选择")), 1, CodeNotRunning)
		dispatcher.Close()
	}
}
