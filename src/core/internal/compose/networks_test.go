package compose

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// The networks a user might add, one of each kind.
func testNetworks() []*controlpb.MeshNetwork {
	return []*controlpb.MeshNetwork{
		{Id: "ts1", Name: "家里", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{
			AuthKey: "tskey-auth-x", Hostname: "laptop"}}},
		{Id: "zt1", Name: "公司", Via: "proxy", Network: &controlpb.MeshNetwork_Zerotier{Zerotier: &controlpb.ZeroTierNetwork{
			NetworkId: "8056C2E21C000001"}}},
		{Id: "et1", Name: "实验室", Network: &controlpb.MeshNetwork_Easytier{Easytier: &controlpb.EasyTierNetwork{
			NetworkName: "lab", NetworkSecret: "s3cret", Peers: []string{"tcp://peer.example:11010"}}}},
	}
}

func composeNetworks(t *testing.T, plan string, networks []*controlpb.MeshNetwork, extra ...Source) (*Result, error) {
	t.Helper()
	return Compose(Composition{
		Sources:    append(sources(), extra...),
		InfoFilter: testInfoFilter,
		Plan:       []byte(plan),
		Mesh:       networks,
	})
}

func marshalled(t *testing.T, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestNetworksAreOutboundsOfTheirOwn(t *testing.T) {
	result, err := composeNetworks(t, `{"version": 1}`, testNetworks())
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{
		"家里": `{"accept-routes":true,"auth-key":"tskey-auth-x","hostname":"laptop","name":"家里",` +
			`"state-dir":"mesh/ts1","type":"tailscale","udp":true}`,
		"公司": `{"dialer-proxy":"节点选择","name":"公司","network":"8056c2e21c000001",` +
			`"state-dir":"mesh/zt1","type":"zerotier","udp":true}`,
		"实验室": `{"dhcp":true,"enable-exit-node":false,"name":"实验室","network-name":"lab",` +
			`"network-secret":"s3cret","no-listener":true,"peers":["tcp://peer.example:11010"],` +
			`"state-dir":"mesh/et1","type":"easytier","udp":true}`,
	} {
		if got := marshalled(t, outbound(result, name)); got != want {
			t.Errorf("%s:\n got %s\nwant %s", name, got, want)
		}
	}
	var mesh []string
	for _, m := range result.Report.Mesh {
		mesh = append(mesh, m.Id+" "+m.Name+" "+m.Type+" "+m.SourceId)
	}
	want := []string{"mesh:ts1 家里 Tailscale ", "mesh:zt1 公司 ZeroTier ", "mesh:et1 实验室 EasyTier "}
	if !slices.Equal(mesh, want) {
		t.Fatalf("report.mesh = %q", mesh)
	}
	// No group takes them in, and no provider holds them.
	if groups := marshalled(t, result.Config["proxy-groups"]); strings.Contains(groups, "家里") {
		t.Fatalf("a group holds a network: %s", groups)
	}
	if got := warnings(result); len(got) != 0 {
		t.Fatalf("warnings = %v", got)
	}
}

func TestNetworkSettingsThatAreOptional(t *testing.T) {
	networks := []*controlpb.MeshNetwork{
		{Id: "ts", Name: "Headscale", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{
			ControlUrl: "https://hs.example"}}},
		{Id: "zt", Name: "ZT", Network: &controlpb.MeshNetwork_Zerotier{Zerotier: &controlpb.ZeroTierNetwork{
			NetworkId: "8056c2e21c000001", IdentitySecret: "abcdef0123:0:pub:priv"}}},
		{Id: "et", Name: "ET", Network: &controlpb.MeshNetwork_Easytier{Easytier: &controlpb.EasyTierNetwork{
			NetworkName: "lab", NetworkSecret: "s", Peers: []string{"udp://p.example:11010"},
			Ipv4: "10.144.144.2/24", Hostname: "laptop"}}},
	}
	result, err := composeNetworks(t, `{"version": 1}`, networks)
	if err != nil {
		t.Fatal(err)
	}
	if got := outbound(result, "Headscale"); got["control-url"] != "https://hs.example" || got["auth-key"] != nil {
		t.Errorf("Headscale = %v", got)
	}
	if got := outbound(result, "ZT"); got["identity-secret"] != "abcdef0123:0:pub:priv" {
		t.Errorf("ZT = %v", got)
	}
	if got := outbound(result, "ET"); got["ipv4"] != "10.144.144.2/24" || got["dhcp"] != nil || got["hostname"] != "laptop" {
		t.Errorf("ET = %v", got)
	}
}

func TestNetworkTargets(t *testing.T) {
	plan := `{"version": 1, "rules": [
	  {"id": "home", "items": [{"type": "IP-CIDR", "value": "100.64.0.0/10", "no_resolve": true}], "target": "mesh:ts1"},
	  {"id": "gone", "items": [{"type": "IP-CIDR", "value": "10.0.0.0/8"}], "target": "mesh:deleted"}],
	  "fallback": "mesh:et1"}`
	result, err := composeNetworks(t, plan, testNetworks())
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"IP-CIDR,100.64.0.0/10,家里,no-resolve", "IP-CIDR,10.0.0.0/8,REJECT", "MATCH,实验室"}
	if got := composedRules(result); !slices.Equal(got, want) {
		t.Fatalf("rules = %v", got)
	}
	if got := warnings(result); !slices.Equal(got, []string{"gone mesh_missing mesh:deleted"}) {
		t.Fatalf("warnings = %v", got)
	}
}

func TestNetworkNamesComeBeforeTheNodes(t *testing.T) {
	plan := `{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "公司"}]}`
	networks := testNetworks()
	networks = append(networks,
		&controlpb.MeshNetwork{Id: "ts2", Name: "家里", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}},
		&controlpb.MeshNetwork{Id: "ts3", Name: "香港 01", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}},
		&controlpb.MeshNetwork{Id: "ts4", Name: "WARP 出口", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}},
	)
	result, err := composeNetworks(t, plan, networks)
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]string{}
	for _, m := range result.Report.Mesh {
		names[m.Id] = m.Name
	}
	for id, want := range map[string]string{
		"mesh:zt1": "公司 · ZeroTier",  // a group has the name
		"mesh:ts2": "家里 · Tailscale", // an earlier network has it
		"mesh:ts3": "香港 01",          // the node gives way
		"mesh:ts4": "WARP 出口 · Tailscale",
	} {
		if names[id] != want {
			t.Errorf("%s is named %q, want %q", id, names[id], want)
		}
	}
	var pool []string
	for _, n := range result.Report.Nodes {
		pool = append(pool, n.Name)
	}
	if !slices.Contains(pool, "香港 01 · 机场 A") || slices.Contains(pool, "香港 01") {
		t.Fatalf("pool = %v", pool)
	}
}

func TestNetworkVia(t *testing.T) {
	networks := testNetworks()
	networks[0].Via = "region:HK"
	networks[1].Via = "region:KR" // no nodes there
	networks[2].Via = "custom-gone"
	result, err := composeNetworks(t, `{"version": 1}`, networks)
	if err != nil {
		t.Fatal(err)
	}
	if got := outbound(result, "家里")["dialer-proxy"]; got != "🇭🇰 香港" {
		t.Errorf("家里 dials through %v", got)
	}
	for _, name := range []string{"公司", "实验室"} {
		if got := outbound(result, name)["dialer-proxy"]; got != "节点选择" {
			t.Errorf("%s dials through %v", name, got)
		}
	}
	want := []string{"mesh:zt1 via_empty region:KR", "mesh:et1 via_empty custom-gone"}
	if got := warnings(result); !slices.Equal(got, want) {
		t.Fatalf("warnings = %v", got)
	}
}

func TestNetworksThatAreRefused(t *testing.T) {
	tailscale := func(settings *controlpb.TailscaleNetwork) *controlpb.MeshNetwork {
		return &controlpb.MeshNetwork{Id: "x", Name: "X", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: settings}}
	}
	easytier := func(settings *controlpb.EasyTierNetwork) *controlpb.MeshNetwork {
		return &controlpb.MeshNetwork{Id: "x", Name: "X", Network: &controlpb.MeshNetwork_Easytier{Easytier: settings}}
	}
	for _, c := range []struct {
		name     string
		networks []*controlpb.MeshNetwork
	}{
		{"an id that is a path", []*controlpb.MeshNetwork{{Id: "../x", Name: "X", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}}}},
		{"no id", []*controlpb.MeshNetwork{{Name: "X", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}}}},
		{"an id twice", []*controlpb.MeshNetwork{tailscale(&controlpb.TailscaleNetwork{}), tailscale(&controlpb.TailscaleNetwork{})}},
		{"a comma in the name", []*controlpb.MeshNetwork{{Id: "x", Name: "a,b", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}}}},
		{"no type", []*controlpb.MeshNetwork{{Id: "x", Name: "X"}}},
		{"a control server that is no URL", []*controlpb.MeshNetwork{tailscale(&controlpb.TailscaleNetwork{ControlUrl: "file:///etc"})}},
		{"a ZeroTier id that is short", []*controlpb.MeshNetwork{{Id: "x", Name: "X", Network: &controlpb.MeshNetwork_Zerotier{Zerotier: &controlpb.ZeroTierNetwork{NetworkId: "8056c2e2"}}}}},
		{"EasyTier without a secret", []*controlpb.MeshNetwork{easytier(&controlpb.EasyTierNetwork{NetworkName: "n", Peers: []string{"tcp://p:1"}})}},
		{"EasyTier without peers", []*controlpb.MeshNetwork{easytier(&controlpb.EasyTierNetwork{NetworkName: "n", NetworkSecret: "s"})}},
		{"EasyTier with an IPv6 address", []*controlpb.MeshNetwork{easytier(&controlpb.EasyTierNetwork{NetworkName: "n", NetworkSecret: "s", Peers: []string{"tcp://p:1"}, Ipv4: "fd00::1/64"})}},
		{"a via that is no group", []*controlpb.MeshNetwork{{Id: "x", Name: "X", Via: "DIRECT", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}}}},
		{"a via that is a mesh outbound", []*controlpb.MeshNetwork{{Id: "x", Name: "X", Via: "mesh:y", Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{}}}}},
	} {
		_, err := composeNetworks(t, `{"version": 1}`, c.networks)
		if err == nil || !strings.HasPrefix(err.Error(), "mesh network ") {
			t.Errorf("%s: err = %v", c.name, err)
		}
	}
}

func TestSharedPlansNameNetworks(t *testing.T) {
	plan := `{"version": 1, "rules": [{"id": "home", "items": [{"type": "DOMAIN-SUFFIX", "value": "ts.net"}], "target": "mesh:ts1"}]}`
	exported, err := Export(Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(plan), Mesh: testNetworks()})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(exported), "DOMAIN-SUFFIX,ts.net,家里") {
		t.Fatalf("exported:\n%s", exported)
	}
}
