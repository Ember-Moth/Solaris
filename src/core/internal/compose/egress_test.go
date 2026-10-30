package compose

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// A WARP device as the UI keeps it; the keys only need to reach the configuration here.
func warpAccount(protocol controlpb.WarpProtocol) *controlpb.WarpAccount {
	return &controlpb.WarpAccount{
		DeviceId:      "a1b2c3d4-0000-4000-8000-000000000001",
		Token:         "t0k3n",
		Protocol:      protocol,
		PrivateKey:    "cHJpdmF0ZQ==",
		PeerPublicKey: "cGVlcg==",
		Endpoint:      "162.159.198.1:443",
		AddressV4:     "172.16.0.2",
		AddressV6:     "2606:4700:110:8a36::2",
		ClientId:      "U4An",
	}
}

func composeWith(t *testing.T, plan string, account *controlpb.WarpAccount, extra ...Source) (*Result, error) {
	t.Helper()
	return Compose(Composition{
		Sources:    append(sources(), extra...),
		InfoFilter: testInfoFilter,
		Plan:       []byte(plan),
		Warp:       account,
	})
}

// outbound is the top-level proxy of that name.
func outbound(result *Result, name string) map[string]any {
	proxies, _ := result.Config["proxies"].([]any)
	for _, raw := range proxies {
		if proxy := raw.(map[string]any); proxy["name"] == name {
			return proxy
		}
	}
	return nil
}

func warnings(result *Result) []string {
	var out []string
	for _, w := range result.Report.Warnings {
		out = append(out, w.EntryId+" "+w.Reason+" "+w.Target)
	}
	return out
}

func composedRules(result *Result) []string {
	var out []string
	for _, raw := range result.Config["rules"].([]any) {
		out = append(out, raw.(string))
	}
	return out
}

const warpPlan = `{"version": 1, "rules": [
  {"id": "ai", "items": [{"type": "DOMAIN-SUFFIX", "value": "openai.com"}], "target": "egress:warp"},
  {"id": "off", "enabled": false, "items": [{"type": "DOMAIN", "value": "off.example"}], "target": "egress:warp"}]%s}`

func TestWarpExitOverMASQUE(t *testing.T) {
	result, err := composeWith(t, strings.Replace(warpPlan, "%s", "", 1), warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_UNSPECIFIED))
	if err != nil {
		t.Fatal(err)
	}
	if got := warnings(result); len(got) != 0 {
		t.Fatalf("warnings = %v", got)
	}
	if got := composedRules(result); got[0] != "DOMAIN-SUFFIX,openai.com,WARP 出口" {
		t.Fatalf("rules = %v", got)
	}
	got, _ := json.Marshal(outbound(result, "WARP 出口"))
	want := `{"dialer-proxy":"节点选择","dns":["1.1.1.1","2606:4700:4700::1111"],"ip":"172.16.0.2",` +
		`"ipv6":"2606:4700:110:8a36::2","mtu":1280,"name":"WARP 出口","network":"h2","port":443,` +
		`"private-key":"cHJpdmF0ZQ==","public-key":"cGVlcg==","remote-dns-resolve":true,` +
		`"server":"162.159.198.1","type":"masque","udp":true}`
	if string(got) != want {
		t.Fatalf("outbound = %s\nwant %s", got, want)
	}

	h3, err := composeWith(t, strings.Replace(warpPlan, "%s", `, "locale": "en"`, 1), warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H3))
	if err != nil {
		t.Fatal(err)
	}
	exit := outbound(h3, "WARP exit")
	if exit == nil || exit["type"] != "masque" || exit["network"] != nil || exit["dialer-proxy"] != "Proxy" {
		t.Fatalf("HTTP/3 outbound = %v", exit)
	}
}

func TestWarpExitOverWireGuard(t *testing.T) {
	account := warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD)
	account.Endpoint = "162.159.192.7:2408"
	plan := strings.Replace(warpPlan, "%s", `, "fallback": "egress:warp", "egress": {"warp": {"via": "region:JP"}}`, 1)
	result, err := composeWith(t, plan, account)
	if err != nil {
		t.Fatal(err)
	}
	exit := outbound(result, "WARP 出口")
	if exit["type"] != "wireguard" || exit["port"] != 2408 || exit["network"] != nil ||
		!slices.Equal(exit["reserved"].([]any), []any{83, 128, 39}) || exit["dialer-proxy"] != "🇯🇵 日本" {
		t.Fatalf("outbound = %v", exit)
	}
	if got := composedRules(result); got[len(got)-1] != "MATCH,WARP 出口" {
		t.Fatalf("rules = %v", got)
	}

	account.ClientId = "AAAAAA=="
	if _, err := composeWith(t, plan, account); err == nil || !strings.Contains(err.Error(), "warp account") {
		t.Fatalf("a client_id of four bytes: %v", err)
	}
}

func TestWarpExitWithoutADeviceRejects(t *testing.T) {
	result, err := composeWith(t, strings.Replace(warpPlan, "%s", `, "fallback": "egress:warp"`, 1), nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := composedRules(result); got[0] != "DOMAIN-SUFFIX,openai.com,REJECT" || got[len(got)-1] != "MATCH,REJECT" {
		t.Fatalf("rules = %v", got)
	}
	want := []string{"ai egress_unavailable egress:warp", "fallback egress_unavailable egress:warp"}
	if got := warnings(result); !slices.Equal(got, want) {
		t.Fatalf("warnings = %v, want %v", got, want)
	}
	if result.Config["proxies"] != nil {
		t.Fatalf("proxies = %v", result.Config["proxies"])
	}

	// A device alone makes no outbound: only a rule that goes to the exit does.
	unused, err := composeWith(t, `{"version": 1}`, warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2))
	if err != nil || unused.Config["proxies"] != nil {
		t.Fatalf("no rule for the exit: %v, %v", unused.Config["proxies"], err)
	}
	// Nor does a device with a broken endpoint fail anything until a rule uses it.
	broken := warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2)
	broken.Endpoint = "162.159.198.1"
	if _, err := composeWith(t, `{"version": 1}`, broken); err != nil {
		t.Fatal(err)
	}
	if _, err := composeWith(t, strings.Replace(warpPlan, "%s", "", 1), broken); err == nil {
		t.Fatal("an endpoint without a port was accepted")
	}
}

func TestWarpViaFallsBackToProxy(t *testing.T) {
	account := warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2)
	for via, want := range map[string]string{
		"g-empty":   "egress:warp via_empty g-empty",
		"region:KR": "egress:warp via_empty region:KR",
	} {
		plan := strings.Replace(warpPlan, "%s", `, "groups": [{"id": "g-empty", "kind": "custom", "name": "Empty",
			"members": {"regions": ["KR"]}}], "egress": {"warp": {"via": "`+via+`"}}`, 1)
		result, err := composeWith(t, plan, account)
		if err != nil {
			t.Fatal(err)
		}
		if got := warnings(result); !slices.Equal(got, []string{want}) {
			t.Errorf("via %s: warnings = %v", via, got)
		}
		if exit := outbound(result, "WARP 出口"); exit["dialer-proxy"] != "节点选择" {
			t.Errorf("via %s: dialer-proxy = %v", via, exit["dialer-proxy"])
		}
	}
	for _, via := range []string{"DIRECT", "region:*", "g-none", "region:XX"} {
		plan := strings.Replace(warpPlan, "%s", `, "egress": {"warp": {"via": "`+via+`"}}`, 1)
		if _, err := composeWith(t, plan, account); err == nil || !strings.Contains(err.Error(), "egress.warp.via") {
			t.Errorf("via %s: %v", via, err)
		}
	}
}

func TestWarpExitNameIsReserved(t *testing.T) {
	clash := Source{ID: "c", Name: "机场 C", YAML: []byte("proxies:\n  - {name: WARP 出口, type: http, server: g.example, port: 8}\n")}
	result, err := composeWith(t, strings.Replace(warpPlan, "%s", "", 1), warpAccount(controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2), clash)
	if err != nil {
		t.Fatal(err)
	}
	last := result.Report.Nodes[len(result.Report.Nodes)-1]
	if last.OriginalName != "WARP 出口" || last.Name != "WARP 出口 · 机场 C" {
		t.Fatalf("the clashing node is %+v", last)
	}
	if outbound(result, "WARP 出口")["type"] != "masque" {
		t.Fatal("the exit lost its name")
	}
	_, err = composeWith(t, `{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "WARP 出口"}]}`, nil)
	if err == nil || !strings.Contains(err.Error(), "taken") {
		t.Fatalf("a group named as the exit: %v", err)
	}
}

const meshSource = `
proxies:
  - {name: 家里, type: tailscale, auth-key: tskey-auth-x, hostname: solaris}
  - {name: 香港 01, type: ss, server: h.example, port: 9, cipher: aes-128-gcm, password: x}
  - {name: 公司, type: zerotier, network: 8056c2e21c000001}
  - {name: 实验室, type: easytier, network-name: lab, network-secret: s}
  - {name: 剩余流量 1 GB, type: easytier, network-name: info, network-secret: s}
`

func TestMeshOutboundsStayOutOfThePool(t *testing.T) {
	mesh := Source{ID: "m", Name: "组网", YAML: []byte(meshSource)}
	plan := `{"version": 1, "groups": [
	  {"id": "g-src", "kind": "custom", "name": "From m", "members": {"sources": ["m"], "nodes": [{"source": "m", "name": "公司"}]}},
	  {"id": "g-name", "kind": "custom", "name": "Named", "members": {"name": {"mode": "regex", "pattern": "."}}}],
	  "rules": [
	  {"id": "ts", "items": [{"type": "IP-CIDR", "value": "100.64.0.0/10", "no_resolve": true}], "target": "mesh:m/家里"},
	  {"id": "lab", "items": [{"type": "DOMAIN-SUFFIX", "value": "lab.internal"}], "target": "mesh:m/实验室"},
	  {"id": "gone", "items": [{"type": "DOMAIN-SUFFIX", "value": "gone.internal"}], "target": "mesh:m/没了"}]}`
	result, err := composeWith(t, plan, nil, mesh)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, m := range result.Report.Mesh {
		got = append(got, m.Id+" → "+m.Name+" "+m.Type+" "+m.SourceId)
	}
	// The pool already has a 香港 01, so the mesh source's node is renamed; the mesh
	// outbounds keep their names, and the info filter does not apply to them.
	want := []string{
		"mesh:m/家里 → 家里 Tailscale m",
		"mesh:m/公司 → 公司 ZeroTier m",
		"mesh:m/实验室 → 实验室 EasyTier m",
		"mesh:m/剩余流量 1 GB → 剩余流量 1 GB EasyTier m",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("mesh = %v\nwant %v", got, want)
	}
	for _, n := range result.Report.Nodes {
		if n.SourceId == "m" && n.OriginalName != "香港 01" {
			t.Errorf("%s is in the pool", n.OriginalName)
		}
	}
	for _, name := range []string{"家里", "公司", "实验室"} {
		if outbound(result, name) == nil {
			t.Errorf("%s is not a top-level proxy", name)
		}
	}
	payload := result.Config["proxy-providers"].(map[string]any)["pool:m"].(map[string]any)["payload"].([]any)
	if len(payload) != 1 || payload[0].(map[string]any)["name"] != "香港 01 · 组网" {
		t.Fatalf("pool:m holds %v", payload)
	}
	// No group condition reaches them: only the node passes, by source and by name.
	for _, name := range []string{"From m", "Named"} {
		filter, _ := groupConfig(t, result, name)["filter"].(string)
		if strings.Contains(filter, "家里") || strings.Contains(filter, "公司") || strings.Contains(filter, "实验室") {
			t.Errorf("%s takes a mesh outbound: %s", name, filter)
		}
	}
	if members := reportGroup(result, "g-src"); members.NodeCount != 1 {
		t.Errorf("From m has %d nodes", members.NodeCount)
	}

	lines := composedRules(result)
	if lines[0] != "IP-CIDR,100.64.0.0/10,家里,no-resolve" || lines[1] != "DOMAIN-SUFFIX,lab.internal,实验室" ||
		lines[2] != "DOMAIN-SUFFIX,gone.internal,REJECT" {
		t.Fatalf("rules = %v", lines)
	}
	if got := warnings(result); !slices.Equal(got, []string{"gone mesh_missing mesh:m/没了"}) {
		t.Fatalf("warnings = %v", got)
	}

	// A mesh outbound's name is taken like a node's.
	clash := Source{ID: "n", Name: "另一个", YAML: []byte("proxies:\n  - {name: 家里, type: tailscale}\n")}
	renamed, err := composeWith(t, `{"version": 1}`, nil, mesh, clash)
	if err != nil {
		t.Fatal(err)
	}
	if last := renamed.Report.Mesh[len(renamed.Report.Mesh)-1]; last.Id != "mesh:n/家里" || last.Name != "家里 · 另一个" {
		t.Fatalf("the clashing mesh outbound is %+v", last)
	}
}

func TestMeshTargetsAreNotMembers(t *testing.T) {
	for _, plan := range []string{
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G", "members": {"others": ["mesh:m/家里"]}}]}`,
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G", "members": {"others": ["egress:warp"]}}]}`,
		`{"version": 1, "fallback": "mesh:"}`,
		`{"version": 1, "fallback": "mesh:a b"}`,
		`{"version": 1, "fallback": "mesh:/家里"}`,
		`{"version": 1, "fallback": "egress:tor"}`,
	} {
		if _, err := ParsePlan([]byte(plan)); err == nil {
			t.Errorf("%s was accepted", plan)
		}
	}
}

func TestInspectMarksMeshOutbounds(t *testing.T) {
	inspected, err := Inspect([]byte(meshSource), testInfoFilter)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, n := range inspected.Nodes {
		got = append(got, n.Name+" "+n.Type+" mesh="+boolText(n.Mesh)+" info="+boolText(n.Info))
	}
	want := []string{
		"家里 Tailscale mesh=1 info=0",
		"香港 01 Shadowsocks mesh=0 info=0",
		"公司 ZeroTier mesh=1 info=0",
		"实验室 EasyTier mesh=1 info=0",
		"剩余流量 1 GB EasyTier mesh=1 info=0",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("nodes = %v\nwant %v", got, want)
	}
}

func boolText(b bool) string {
	if b {
		return "1"
	}
	return "0"
}

// An exported plan names the exit and the mesh outbounds, and converting it back finds
// them again.
func TestExportAndConvertKeepExitsAndMesh(t *testing.T) {
	mesh := Source{ID: "m", Name: "组网", YAML: []byte(meshSource)}
	plan := strings.Replace(warpPlan, "%s", `, "fallback": "mesh:m/家里"`, 1)
	exported, err := Export(Composition{Sources: append(sources(), mesh), InfoFilter: testInfoFilter, Plan: []byte(plan)})
	if err != nil {
		t.Fatal(err)
	}
	text := string(exported)
	if !strings.Contains(text, "DOMAIN-SUFFIX,openai.com,WARP 出口") || !strings.Contains(text, "MATCH,家里") {
		t.Fatalf("export:\n%s", text)
	}
	// The mesh source's own copy, with the rules: its outbounds convert to its mesh targets.
	converted, err := Convert([]byte(meshSource+text), Conversion{SourceID: "m", Locale: "zh"})
	if err != nil {
		t.Fatal(err)
	}
	reread, err := ParsePlan([]byte(converted.PlanJson))
	if err != nil {
		t.Fatal(err)
	}
	if reread.Rules[0].Target != EgressWarp || reread.Fallback != "mesh:m/家里" || len(converted.Skipped) != 0 {
		t.Fatalf("converted %s, skipped %v", converted.PlanJson, converted.Skipped)
	}
	// Without a source there is no mesh target to name, so the rule is skipped.
	imported, err := Convert([]byte(meshSource+text), Conversion{Locale: "zh"})
	if err != nil {
		t.Fatal(err)
	}
	if len(imported.Skipped) != 1 || imported.Skipped[0].Reason != SkipTarget {
		t.Fatalf("imported %s, skipped %v", imported.PlanJson, imported.Skipped)
	}
}
