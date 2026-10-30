package engine

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const cheapPlan = `{"version": 1, "groups": [
	{"id": "cheap", "kind": "custom", "name": "低倍率", "members": {
	  "regions": ["HK"], "exclude": {"mode": "keyword", "pattern": "0.1X"},
	  "nodes": [{"source": "a", "name": "🇯🇵 东京 01"}], "others": ["DIRECT"]}}]}`

// The editor's preview lists the members mihomo's group gets (docs/routing.md §10).
func TestPreviewMatchesTheComposedGroup(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	preview, err := engine.PreviewGroup(*composition(cheapPlan), "cheap")
	if err != nil {
		t.Fatal(err)
	}
	admitted, err := admitProfile(admission.Profile{Composition: composition(cheapPlan)}, noTUN)
	if err != nil {
		t.Fatal(err)
	}
	var want []string
	for _, member := range members(t, admitted.cfg, "低倍率") {
		if member != "DIRECT" {
			want = append(want, member)
		}
	}
	var got []string
	for _, node := range preview.GetNodes() {
		got = append(got, node.GetName())
	}
	if !slices.Equal(got, want) || !slices.Equal(preview.GetOthers(), []string{"DIRECT"}) {
		t.Fatalf("preview %v and %v, group %v", got, preview.GetOthers(), want)
	}
	if _, err := engine.PreviewGroup(*composition(`{"version": 2}`), "cheap"); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("a broken plan: %v", err)
	}
}

const matchPlan = `{"version": 1, "groups": [{"id": "pass", "kind": "custom", "name": "Pass", "members": {"nodes": [{"source": "a", "name": "香港 01"}]}}],
	"rules": [
	  {"id": "web", "items": [{"type": "DOMAIN-SUFFIX", "value": "example.com"}], "target": "region:JP"},
	  {"id": "lan", "items": [{"type": "IP-CIDR", "value": "10.0.0.0/8", "no_resolve": true}], "target": "DIRECT"},
	  {"id": "tg", "items": [{"type": "GEOSITE", "value": "telegram"}], "target": "REJECT"},
	  {"id": "kr", "items": [{"type": "DOMAIN", "value": "kr.example"}], "target": "region:KR"},
	  {"id": "quic", "items": [{"type": "NETWORK", "value": "udp"}], "target": "REJECT"},
	  {"id": "local", "items": [{"type": "IP-CIDR", "value": "127.0.0.0/8"}], "target": "auto"}],
	"fallback": "proxy"}`

func TestMatchRuleOnADraft(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	draft := &admission.Profile{
		Composition: composition(matchPlan),
		Overrides:   [][]byte{[]byte("prepend-rules: ['DOMAIN,blocked.example,REJECT']\n")},
	}
	for _, test := range []struct {
		host        string
		udp         bool
		index       uint32
		entry, kind string
		chain       []string
	}{
		{"WWW.Example.com.", false, 1, "web", "DOMAIN-SUFFIX", []string{"🇯🇵 日本"}},
		{"10.1.2.3", false, 2, "lan", "IP-CIDR", []string{"DIRECT"}},
		{"api.telegram.org", false, 3, "tg", "GEOSITE", []string{"REJECT"}},
		// The empty region's rule goes to 节点选择 meanwhile.
		{"kr.example", false, 4, "kr", "DOMAIN", []string{"节点选择"}},
		{"blocked.example", false, 0, "", "DOMAIN", []string{"REJECT"}},
		{"other.example", true, 5, "quic", "NETWORK", []string{"REJECT"}},
		// mihomo's hosts table has localhost before any rule looks.
		{"localhost", false, 6, "local", "IP-CIDR", []string{"自动选择"}},
		{"other.example", false, 7, compose.FallbackEntry, "MATCH", []string{"节点选择"}},
	} {
		matched, err := engine.MatchRule(MatchRequest{Draft: draft, Host: test.host, Port: 443, UDP: test.udp})
		if err != nil {
			t.Fatalf("%s: %v", test.host, err)
		}
		rule := matched.GetRule()
		if !matched.GetMatched() || matched.GetIndex() != test.index || rule.GetEntryId() != test.entry ||
			rule.GetType() != test.kind || !slices.Equal(matched.GetChain(), test.chain) {
			t.Errorf("%s: %v", test.host, matched)
		}
	}
	if _, err := engine.MatchRule(MatchRequest{Host: "example.com"}); !errors.Is(err, ErrNotRunning) {
		t.Errorf("the running configuration while stopped: %v", err)
	}
	for _, host := range []string{"", "a b", "http://example.com"} {
		if _, err := engine.MatchRule(MatchRequest{Draft: draft, Host: host}); !errors.Is(err, ErrInvalidArgument) {
			t.Errorf("host %q: %v", host, err)
		}
	}
	nothing := &admission.Profile{Config: []byte("proxies: []\nrules: ['DOMAIN,only.example,DIRECT']\n")}
	if matched, err := engine.MatchRule(MatchRequest{Draft: nothing, Host: "else.example"}); err != nil ||
		matched.GetMatched() || !slices.Equal(matched.GetChain(), []string{"DIRECT"}) {
		t.Errorf("no rule matched: %v, %v", matched, err)
	}
}

// The rule test says what an actual connection matches, down to the node (docs/routing.md §10).
func TestMatchRuleAgreesWithConnections(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	server, release := slowTarget(t)
	port := freePort(t)
	address := server.Listener.Addr().String()
	serverPort := address[strings.LastIndex(address, ":")+1:]
	plan := fmt.Sprintf(`{"version": 1, "test_url": %q, "rules": [
		{"id": "web", "items": [{"type": "DST-PORT", "value": %q}], "target": "region:JP"}],
		"fallback": "DIRECT"}`, server.URL+"/generate_204", serverPort)
	profile := admission.Profile{Composition: composition(plan), Options: admission.RunOptions{MixedPort: uint16(port)}}
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	var portNumber uint16
	fmt.Sscan(serverPort, &portNumber)
	draft, err := engine.MatchRule(MatchRequest{Draft: &profile, Host: "127.0.0.1", Port: portNumber})
	if err != nil {
		t.Fatal(err)
	}
	running, err := engine.MatchRule(MatchRequest{Host: "127.0.0.1", Port: portNumber})
	if err != nil {
		t.Fatal(err)
	}

	done := make(chan string, 1)
	go func() {
		proxy, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", port))
		client := &http.Client{Transport: &http.Transport{Proxy: http.ProxyURL(proxy)}, Timeout: 10 * time.Second}
		response, err := client.Get(server.URL + "/slow")
		if err != nil {
			done <- err.Error()
			return
		}
		defer response.Body.Close()
		body, _ := io.ReadAll(response.Body)
		done <- string(body)
	}()
	var open *controlpb.Connection
	for deadline := time.Now().Add(5 * time.Second); open == nil && time.Now().Before(deadline); {
		for _, connection := range engine.Connections().GetConnections() {
			open = connection
		}
		time.Sleep(20 * time.Millisecond)
	}
	if open == nil {
		t.Fatal("no connection")
	}
	for name, matched := range map[string]*controlpb.MatchRuleResponse{"draft": draft, "running": running} {
		rule := matched.GetRule()
		if rule.GetEntryId() != open.GetRuleEntryId() || rule.GetType() != open.GetRule() ||
			rule.GetPayload() != open.GetRulePayload() || !slices.Equal(matched.GetChain(), open.GetChains()) {
			t.Errorf("%s test %v, connection %v", name, matched, open)
		}
	}
	close(release)
	if body := <-done; body != "slow" {
		t.Fatalf("body = %q", body)
	}
}

func TestConvertToPlanKnowsTheCategories(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	converted, err := engine.ConvertToPlan([]byte("rules:\n  - GEOSITE,telegram,DIRECT\n  - GEOSITE,netflix,DIRECT\n"), compose.Conversion{})
	if err != nil {
		t.Fatal(err)
	}
	if skipped := converted.GetSkipped(); converted.GetEntryCount() != 1 || len(skipped) != 1 ||
		skipped[0].GetRule() != "GEOSITE,netflix,DIRECT" {
		t.Fatalf("converted = %v", converted)
	}
	if _, err := engine.ConvertToPlan([]byte("proxies: []\n"), compose.Conversion{}); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("nothing to convert: %v", err)
	}
}

func TestExportPlan(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	withSecret := composition(cheapPlan)
	withSecret.Sources = append(withSecret.Sources, compose.Source{ID: "c", Name: "C",
		YAML: []byte("proxies:\n  - {name: 日本 09, type: ss, server: c.example, port: 9, cipher: aes-128-gcm, password: hunter2}\n")})
	profile := admission.Profile{
		Composition: withSecret,
		Overrides:   [][]byte{[]byte("prepend-rules: ['DOMAIN,blocked.example,REJECT']\nexternal-controller: 127.0.0.1:9090\n")},
		Options:     admission.RunOptions{MixedPort: 17891, TUN: true},
	}
	generated, err := engine.ExportPlan(profile, controlpb.ExportFormat_EXPORT_FORMAT_GENERATED)
	if err != nil {
		t.Fatal(err)
	}
	text := string(generated)
	for _, want := range []string{"mode: rule\n", "mixed-port: 17891\n", "- DOMAIN,blocked.example,REJECT\n",
		"external-controller: \"\"\n", "name: 香港 01\n", "server: ", "- name: 低倍率\n"} {
		if !strings.Contains(text, want) {
			t.Errorf("the generated configuration lacks %q", want)
		}
	}
	// No TUN on this host, and no node's password.
	if strings.Contains(text, "hunter2") || strings.Contains(text, "cipher") || !strings.Contains(text, "c.example") ||
		strings.Contains(text, "tun:\n    enable: true") {
		t.Errorf("the generated configuration:\n%s", text)
	}
	shared, err := engine.ExportPlan(profile, controlpb.ExportFormat_EXPORT_FORMAT_SHARE)
	if err != nil || !strings.HasPrefix(string(shared), "proxy-groups:\n") || strings.Contains(string(shared), "blocked.example") {
		t.Fatalf("shared = %s, %v", shared, err)
	}
	if _, err := engine.ExportPlan(admission.Profile{Config: []byte("mode: rule\n")}, 0); !errors.Is(err, ErrInvalidArgument) {
		t.Fatalf("a profile's own YAML: %v", err)
	}
}

// What the generated configuration shows of a node.
func TestReduceNodes(t *testing.T) {
	doc := map[string]any{
		"proxies": []any{map[string]any{"name": "a", "type": "ss", "server": "s", "port": 1, "password": "secret"}},
		"proxy-providers": map[string]any{"pool:a": map[string]any{"type": "inline", "payload": []any{
			map[string]any{"name": "b", "type": "vless", "server": "t", "port": 2, "uuid": "secret"}}}},
	}
	reduceNodes(doc)
	if text := fmt.Sprint(doc); strings.Contains(text, "secret") || !strings.Contains(text, "server:t") {
		t.Fatalf("reduced = %s", text)
	}
}
