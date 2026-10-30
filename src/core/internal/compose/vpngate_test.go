package compose

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// vpnGateServer is a server as ListVpnGate hands it out; nothing dials it here.
func vpnGateServer(host, country, ip string) *controlpb.VpnGateServer {
	return &controlpb.VpnGateServer{
		HostName: host,
		Ip:       ip,
		Country:  country,
		Score:    100,
		Openvpn: &controlpb.OpenVpnOptions{
			Server:      ip,
			Port:        443,
			Proto:       "tcp",
			Cipher:      "AES-128-CBC",
			DataCiphers: []string{"AES-128-CBC"},
			Auth:        "SHA1",
			Ca:          "-----BEGIN CERTIFICATE-----\nY2E=\n-----END CERTIFICATE-----\n",
			Cert:        "-----BEGIN CERTIFICATE-----\nY2VydA==\n-----END CERTIFICATE-----\n",
			Key:         "-----BEGIN RSA PRIVATE KEY-----\na2V5\n-----END RSA PRIVATE KEY-----\n",
		},
	}
}

func vpnGateServers() []*controlpb.VpnGateServer {
	return []*controlpb.VpnGateServer{
		vpnGateServer("vpn1", "JP", "203.0.113.1"),
		vpnGateServer("vpn2", "KR", "203.0.113.2"),
	}
}

func composeVpnGate(t *testing.T, plan string, servers []*controlpb.VpnGateServer, extra ...Source) (*Result, error) {
	t.Helper()
	return Compose(Composition{
		Sources:    append(sources(), extra...),
		InfoFilter: testInfoFilter,
		Plan:       []byte(plan),
		VpnGate:    servers,
	})
}

const vpnGatePlan = `{"version": 1, "rules": [
  {"id": "ip", "items": [{"type": "DOMAIN-SUFFIX", "value": "ifconfig.me"}], "target": "egress:vpngate"},
  {"id": "off", "enabled": false, "items": [{"type": "DOMAIN", "value": "off.example"}], "target": "egress:vpngate"}]%s}`

func vpnGatePlanWith(extra string) string { return strings.Replace(vpnGatePlan, "%s", extra, 1) }

func TestVpnGateExitSharesOutSitesOverItsServers(t *testing.T) {
	result, err := composeVpnGate(t, vpnGatePlanWith(`, "egress": {"vpngate": {"via": "region:JP", "countries": ["JP", "KR"], "count": 2}}`), vpnGateServers())
	if err != nil {
		t.Fatal(err)
	}
	if got := warnings(result); len(got) != 0 {
		t.Fatalf("warnings = %v", got)
	}
	if got := composedRules(result); got[0] != "DOMAIN-SUFFIX,ifconfig.me,VPN Gate 出口" {
		t.Fatalf("rules = %v", got)
	}
	got, _ := json.Marshal(outbound(result, "VPN Gate JP vpn1"))
	want := `{"auth":"SHA1","ca":"-----BEGIN CERTIFICATE-----\nY2E=\n-----END CERTIFICATE-----\n",` +
		`"cert":"-----BEGIN CERTIFICATE-----\nY2VydA==\n-----END CERTIFICATE-----\n","cipher":"AES-128-CBC",` +
		`"data-ciphers":["AES-128-CBC"],"dialer-proxy":"🇯🇵 日本","dns":["1.1.1.1","8.8.8.8"],"handshake-timeout":15,` +
		`"key":"-----BEGIN RSA PRIVATE KEY-----\na2V5\n-----END RSA PRIVATE KEY-----\n","name":"VPN Gate JP vpn1",` +
		`"ping":10,"ping-restart":60,"port":443,"proto":"tcp","remote-dns-resolve":true,"server":"203.0.113.1",` +
		`"type":"openvpn","udp":true}`
	if string(got) != want {
		t.Fatalf("outbound = %s\nwant %s", got, want)
	}
	group, _ := json.Marshal(groupConfig(t, result, "VPN Gate 出口"))
	wantGroup := `{"interval":120,"lazy":true,"max-failed-times":2,"name":"VPN Gate 出口",` +
		`"proxies":["VPN Gate JP vpn1","VPN Gate KR vpn2"],"strategy":"consistent-hashing","timeout":5000,` +
		`"type":"load-balance"}`
	if string(group) != wantGroup {
		t.Fatalf("group = %s\nwant %s", group, wantGroup)
	}
	// The exit is no group of the plan's: the report lists the plan's groups only.
	for _, g := range result.Report.Groups {
		if g.Name == "VPN Gate 出口" {
			t.Fatal("the exit's group is in the report")
		}
	}
	// The pool's groups never take the servers.
	for _, raw := range result.Config["proxy-groups"].([]any) {
		g := raw.(map[string]any)
		if g["name"] != "VPN Gate 出口" && strings.Contains(g["name"].(string)+groupMembers(g), "vpn1") {
			t.Fatalf("%v holds a server", g)
		}
	}
}

func groupMembers(group map[string]any) string {
	raw, _ := json.Marshal(group["proxies"])
	return string(raw)
}

func TestVpnGateExitWithOneServerAtATime(t *testing.T) {
	plan := vpnGatePlanWith(`, "locale": "en", "test_url": "https://cp.example/generate_204", "fallback": "egress:vpngate",
		"egress": {"vpngate": {"mode": "single"}}`)
	result, err := composeVpnGate(t, plan, vpnGateServers())
	if err != nil {
		t.Fatal(err)
	}
	group := groupConfig(t, result, "VPN Gate exit")
	if group["type"] != "fallback" || group["strategy"] != nil || group["url"] != "https://cp.example/generate_204" {
		t.Fatalf("group = %v", group)
	}
	if exit := outbound(result, "VPN Gate KR vpn2"); exit["dialer-proxy"] != "Proxy" {
		t.Fatalf("outbound = %v", exit)
	}
	if got := composedRules(result); got[len(got)-1] != "MATCH,VPN Gate exit" {
		t.Fatalf("rules = %v", got)
	}
}

// No servers: the rules that name the exit reject (D21); servers without such a rule make
// neither outbounds nor the group.
func TestVpnGateExitWithoutServersRejects(t *testing.T) {
	result, err := composeVpnGate(t, vpnGatePlanWith(`, "fallback": "egress:vpngate"`), nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := composedRules(result); got[0] != "DOMAIN-SUFFIX,ifconfig.me,REJECT" || got[len(got)-1] != "MATCH,REJECT" {
		t.Fatalf("rules = %v", got)
	}
	want := []string{"ip egress_unavailable egress:vpngate", "fallback egress_unavailable egress:vpngate"}
	if got := warnings(result); !slices.Equal(got, want) {
		t.Fatalf("warnings = %v", got)
	}
	if result.Config["proxies"] != nil {
		t.Fatalf("proxies = %v", result.Config["proxies"])
	}

	unused, err := composeVpnGate(t, `{"version": 1}`, vpnGateServers())
	if err != nil {
		t.Fatal(err)
	}
	if unused.Config["proxies"] != nil || strings.Contains(groupMembers(map[string]any{"proxies": unused.Config["proxy-groups"]}), "VPN Gate") {
		t.Fatalf("an unused exit: %v", unused.Config)
	}
}

func TestVpnGateViaFallsBackToProxy(t *testing.T) {
	plan := vpnGatePlanWith(`, "groups": [{"id": "g-empty", "kind": "custom", "name": "Empty",
		"members": {"regions": ["KR"]}}], "egress": {"vpngate": {"via": "g-empty"}}`)
	result, err := composeVpnGate(t, plan, vpnGateServers())
	if err != nil {
		t.Fatal(err)
	}
	if got := warnings(result); !slices.Equal(got, []string{"egress:vpngate via_empty g-empty"}) {
		t.Fatalf("warnings = %v", got)
	}
	if exit := outbound(result, "VPN Gate JP vpn1"); exit["dialer-proxy"] != "节点选择" {
		t.Fatalf("outbound = %v", exit)
	}
}

func TestVpnGateSettingsAreChecked(t *testing.T) {
	for extra, want := range map[string]string{
		`"via": "DIRECT"`:       "egress.vpngate.via",
		`"via": "region:XX"`:    "egress.vpngate.via",
		`"mode": "round-robin"`: "egress.vpngate.mode",
		`"count": 11`:           "egress.vpngate.count",
		`"countries": ["jp"]`:   "egress.vpngate.countries",
		`"countries": ["JPN"]`:  "egress.vpngate.countries",
	} {
		plan := vpnGatePlanWith(`, "egress": {"vpngate": {` + extra + `}}`)
		if _, err := ParsePlan([]byte(plan)); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%s: %v", extra, err)
		}
	}
	// What the UI sends is the servers it picked: each once, with its options.
	twice := append(vpnGateServers(), vpnGateServer("vpn1", "JP", "203.0.113.9"))
	if _, err := composeVpnGate(t, vpnGatePlanWith(""), twice); err == nil || !strings.Contains(err.Error(), "twice") {
		t.Fatalf("a server twice: %v", err)
	}
	bare := vpnGateServers()
	bare[1].Openvpn = nil
	if _, err := composeVpnGate(t, vpnGatePlanWith(""), bare); err == nil || !strings.Contains(err.Error(), "OpenVPN") {
		t.Fatalf("a server without options: %v", err)
	}
	// The exit is no group a group may hold.
	if _, err := ParsePlan([]byte(`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G",
		"members": {"others": ["egress:vpngate"]}}]}`)); err == nil {
		t.Fatal("the exit as a member")
	}
}

func TestVpnGateNamesAreReserved(t *testing.T) {
	clash := Source{ID: "c", Name: "机场 C", YAML: []byte("proxies:\n" +
		"  - {name: VPN Gate 出口, type: http, server: g.example, port: 8}\n" +
		"  - {name: VPN Gate JP vpn1, type: http, server: g.example, port: 9}\n")}
	result, err := composeVpnGate(t, vpnGatePlanWith(""), vpnGateServers(), clash)
	if err != nil {
		t.Fatal(err)
	}
	var renamed []string
	for _, n := range result.Report.Nodes {
		if n.SourceId == "c" {
			renamed = append(renamed, n.Name)
		}
	}
	if want := []string{"VPN Gate 出口 · 机场 C", "VPN Gate JP vpn1 · 机场 C"}; !slices.Equal(renamed, want) {
		t.Fatalf("the clashing nodes are %v", renamed)
	}
	if outbound(result, "VPN Gate JP vpn1")["type"] != "openvpn" {
		t.Fatal("the server lost its name")
	}
	_, err = composeVpnGate(t, `{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "VPN Gate exit"}], "locale": "en"}`, nil)
	if err == nil || !strings.Contains(err.Error(), "taken") {
		t.Fatalf("a group named as the exit: %v", err)
	}
}

// An exported plan names the exit, and converting it back finds it again.
func TestExportAndConvertKeepTheVpnGateExit(t *testing.T) {
	exported, err := Export(Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(vpnGatePlanWith(""))})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(exported), "DOMAIN-SUFFIX,ifconfig.me,VPN Gate 出口") {
		t.Fatalf("export:\n%s", exported)
	}
	converted, err := Convert([]byte(meshSource+string(exported)), Conversion{Locale: "zh"})
	if err != nil {
		t.Fatal(err)
	}
	reread, err := ParsePlan([]byte(converted.PlanJson))
	if err != nil {
		t.Fatal(err)
	}
	if reread.Rules[0].Target != EgressVpnGate {
		t.Fatalf("converted %s, skipped %v", converted.PlanJson, converted.Skipped)
	}
}
