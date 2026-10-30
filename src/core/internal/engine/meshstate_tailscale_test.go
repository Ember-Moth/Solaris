//go:build with_gvisor && !no_tailscale

package engine

import (
	"net/netip"
	"slices"
	"testing"

	"github.com/metacubex/mihomo/adapter/outbound"
	"github.com/metacubex/tailscale/ipn/ipnstate"
	"github.com/metacubex/tailscale/tsnet"
	"github.com/metacubex/tailscale/types/key"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// The Tailscale probe reads v1.19.33's fields, and leaves an outbound that has not started
// alone: asking tsnet then would start a second server.
func TestMeshTailscaleProbeFindsItsFields(t *testing.T) {
	tailscale, err := outbound.NewTailscale(outbound.TailscaleOption{Name: "ts", StateDir: "mesh/probe"})
	if err != nil {
		t.Fatal(err)
	}
	defer tailscale.Close()
	if client, ready, err := tailscaleClient(tailscale); client != nil || ready || err != nil {
		t.Fatalf("tailscaleClient = %v, %v, %v", client, ready, err)
	}
	if _, err := fieldOf[error](tailscale, "backendInitErr"); err != nil {
		t.Fatal(err)
	}
	if server, err := fieldOf[*tsnet.Server](tailscale, "server"); err != nil || *server == nil {
		t.Fatalf("server = %v, %v", server, err)
	}
}

func TestMeshDescribeTailscale(t *testing.T) {
	online := &ipnstate.PeerStatus{Online: true}
	offline := &ipnstate.PeerStatus{}
	cases := map[string]string{
		"NeedsLogin":       "MESH_STATE_NEEDS_LOGIN https://login.tailscale.com/a/1",
		"NeedsMachineAuth": "MESH_STATE_WAITING ",
		"Running":          "MESH_STATE_ONLINE ",
		"Stopped":          "MESH_STATE_FAILED ",
		"Starting":         "MESH_STATE_STARTING ",
	}
	for state, want := range cases {
		status := &controlpb.MeshStatus{State: controlpb.MeshState_MESH_STATE_STARTING}
		describeTailscale(&ipnstate.Status{BackendState: state, AuthURL: "https://login.tailscale.com/a/1"}, status)
		if got := status.State.String() + " " + status.LoginUrl; got != want {
			t.Errorf("%s: %q", state, got)
		}
	}
	status := &controlpb.MeshStatus{}
	describeTailscale(&ipnstate.Status{
		BackendState:   "Running",
		TailscaleIPs:   []netip.Addr{netip.MustParseAddr("100.101.102.103"), netip.MustParseAddr("fd7a:115c:a1e0::1")},
		Peer:           map[key.NodePublic]*ipnstate.PeerStatus{key.NewNode().Public(): online, key.NewNode().Public(): offline},
		CurrentTailnet: &ipnstate.TailnetStatus{Name: "example.ts.net"},
	}, status)
	if !slices.Equal(status.Addresses, []string{"100.101.102.103/32", "fd7a:115c:a1e0::1/128"}) ||
		status.PeerCount != 1 || status.NetworkName != "example.ts.net" {
		t.Fatalf("running: %v", status)
	}
}
