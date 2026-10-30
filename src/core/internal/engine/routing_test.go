package engine

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/metacubex/mihomo/config"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// A subscription that also asks for what composition must leave behind: its rules, its
// port, a TUN and a controller.
const nodesSource = `
mixed-port: 1234
external-controller: 127.0.0.1:9090
tun: {enable: true}
proxies:
  - {name: 🇯🇵 东京 01, type: direct}
  - {name: 香港 01, type: direct}
  - {name: 香港 02 0.1x, type: direct}
  - {name: 剩余流量 3 GB, type: direct}
rules:
  - MATCH,DIRECT
`

func composition(plan string) *compose.Composition {
	return &compose.Composition{
		Sources: []compose.Source{
			{ID: "a", Name: "A", YAML: []byte(nodesSource)},
			{ID: "b", Name: "B", YAML: []byte("proxies:\n  - {name: 香港 01, type: direct}\n")},
		},
		InfoFilter: "剩余",
		Plan:       []byte(plan),
	}
}

func members(t *testing.T, cfg *config.Config, name string) []string {
	t.Helper()
	proxy, ok := cfg.Proxies[name]
	if !ok {
		t.Fatalf("no group %q", name)
	}
	group, ok := groupOf(proxy)
	if !ok {
		t.Fatalf("%q is not a group", name)
	}
	var names []string
	for _, member := range group.Proxies() {
		names = append(names, member.Name())
	}
	return names
}

// mihomo builds the groups with the members the plan computed (docs/routing.md §4.5).
func TestComposedGroupsHoldThePlannedMembers(t *testing.T) {
	admitted, err := admitProfile(Profile{Composition: composition(`{"version": 1, "groups": [
		{"id": "cheap", "kind": "custom", "name": "低倍率", "members": {
		  "regions": ["HK"], "exclude": {"mode": "keyword", "pattern": "0.1X"},
		  "nodes": [{"source": "a", "name": "香港 02 0.1x"}], "others": ["DIRECT"]}},
		{"id": "rest", "kind": "custom", "name": "其余", "members": {"all": true,
		  "exclude": {"mode": "regex", "pattern": "^香港"}}}]}`)}, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	cfg := admitted.cfg
	for name, want := range map[string][]string{
		"节点选择":  {"自动选择", "🇭🇰 香港", "🇯🇵 日本", "DIRECT"},
		"自动选择":  {"🇯🇵 东京 01", "香港 01", "香港 02 0.1x", "香港 01 · B"},
		"🇭🇰 香港": {"香港 01", "香港 02 0.1x", "香港 01 · B"},
		"🇯🇵 日本": {"🇯🇵 东京 01"},
		// The picked node stays although the exclusion matches it; DIRECT comes first.
		"低倍率": {"DIRECT", "香港 01", "香港 02 0.1x", "香港 01 · B"},
		"其余":  {"🇯🇵 东京 01"},
	} {
		if got := members(t, cfg, name); !slices.Equal(got, want) {
			t.Errorf("%s = %v, want %v", name, got, want)
		}
	}
	if got := admitted.report.GetGroups(); len(got) != 6 {
		t.Errorf("report groups = %v", got)
	}
}

// The pipeline order (docs/routing.md §5.6): the composed configuration, then the
// overrides, then the platform patches and the security items. The subscription only
// lends its nodes.
func TestCompositionGoesThroughThePipeline(t *testing.T) {
	profile := Profile{
		Composition: composition(`{"version": 1, "rules": [
			{"id": "web", "items": [{"type": "DOMAIN-SUFFIX", "value": "example.com"}], "target": "region:JP"}]}`),
		Overrides: [][]byte{
			[]byte("prepend-rules: ['DOMAIN,blocked.example,REJECT']\ndns: {enhanced-mode: redir-host}\n"),
			[]byte("external-controller: 127.0.0.1:9091\ntun: {enable: true, auto-route: true}\nallow-lan: true\n"),
		},
		Options: RunOptions{MixedPort: 17890},
	}
	admitted, err := admitProfile(profile, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	cfg := admitted.cfg
	if cfg.Controller.ExternalController != "" || cfg.General.Tun.Enable || cfg.General.Tun.AutoRoute ||
		cfg.General.AllowLan || cfg.General.MixedPort != 17890 {
		t.Fatalf("controller %q, tun %+v, allow-lan %v, mixed-port %d", cfg.Controller.ExternalController,
			cfg.General.Tun, cfg.General.AllowLan, cfg.General.MixedPort)
	}
	if mode := cfg.DNS.EnhancedMode.String(); mode != "redir-host" {
		t.Errorf("the override's DNS mode did not apply: %s", mode)
	}
	var rules []string
	for _, rule := range cfg.Rules {
		rules = append(rules, ruleKeyword(rule.RuleType().String())+","+rule.Payload()+","+rule.Adapter())
	}
	want := []string{"DOMAIN,blocked.example,REJECT", "DOMAIN-SUFFIX,example.com,🇯🇵 日本", "MATCH,,节点选择"}
	if !slices.Equal(rules, want) {
		t.Fatalf("rules = %v, want %v", rules, want)
	}
	if got := admitted.entries.byIndex; !slices.Equal(got, []string{"", "web", compose.FallbackEntry}) {
		t.Fatalf("entries = %v", got)
	}
}

func TestBrokenCompositionIsAnInvalidConfig(t *testing.T) {
	for name, composition := range map[string]*compose.Composition{
		"plan":         {Plan: []byte(`{"version": 9}`)},
		"subscription": {Sources: []compose.Source{{ID: "a", Name: "A", YAML: []byte("proxies: [\n")}}, Plan: []byte(`{"version": 1}`)},
		// mihomo's own verdicts on the rules.
		"rule":     {Plan: []byte(`{"version": 1, "rules": [{"id": "r", "items": [{"type": "IP-CIDR", "value": "not-a-prefix"}], "target": "DIRECT"}]}`)},
		"category": {Plan: []byte(`{"version": 1, "rules": [{"id": "r", "items": [{"type": "GEOSITE", "value": "no-such-category"}], "target": "DIRECT"}]}`)},
	} {
		_, err := admitProfile(Profile{Composition: composition}, TunNone)
		if !errors.Is(err, ErrInvalidConfig) {
			t.Errorf("%s: %v, want ErrInvalidConfig", name, err)
		}
	}
}

// slowTarget answers /slow only once release closes, so a connection stays open.
func slowTarget(t *testing.T) (*httptest.Server, chan struct{}) {
	t.Helper()
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/generate_204":
			w.WriteHeader(http.StatusNoContent)
		case "/slow":
			w.(http.Flusher).Flush()
			<-release
			_, _ = w.Write([]byte("slow"))
		default:
			_, _ = w.Write([]byte("hello through mihomo"))
		}
	}))
	t.Cleanup(server.Close)
	t.Cleanup(func() {
		select {
		case <-release:
		default:
			close(release)
		}
	})
	return server, release
}

// A composed rule sends traffic to its region; the rule list and the open connection
// name the rule entry.
func TestComposedRulesRouteAndNameTheirEntries(t *testing.T) {
	engine := openEngine(t, TunNone)
	server, release := slowTarget(t)
	port := freePort(t)
	serverPort := server.Listener.Addr().String()[strings.LastIndex(server.Listener.Addr().String(), ":")+1:]
	plan := fmt.Sprintf(`{"version": 1, "test_url": %q, "rules": [
		{"id": "web", "items": [{"type": "DST-PORT", "value": %q}], "target": "region:JP"},
		{"id": "kr", "items": [{"type": "DOMAIN", "value": "kr.example"}], "target": "region:KR"}],
		"fallback": "DIRECT"}`, server.URL+"/generate_204", serverPort)
	profile := Profile{
		Composition: composition(plan),
		Overrides:   [][]byte{[]byte("prepend-rules: ['DOMAIN,blocked.example,REJECT']\n")},
		Options:     RunOptions{MixedPort: uint16(port)},
	}
	report, err := engine.Start(profile)
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	if warnings := report.GetWarnings(); len(warnings) != 1 || warnings[0].GetEntryId() != "kr" ||
		warnings[0].GetReason() != compose.ReasonRegionEmpty {
		t.Fatalf("warnings = %v", warnings)
	}

	var entries []string
	for _, rule := range engine.ListRules() {
		entries = append(entries, rule.GetEntryId())
	}
	if !slices.Equal(entries, []string{"", "web", "kr", compose.FallbackEntry}) {
		t.Fatalf("rule entries = %v", entries)
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
			if connection.GetRule() == "DST-PORT" {
				open = connection
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if open == nil {
		t.Fatal("no connection through the DST-PORT rule")
	}
	if open.GetRuleEntryId() != "web" || !slices.Equal(open.GetChains(), []string{"🇯🇵 东京 01", "🇯🇵 日本"}) {
		t.Fatalf("connection = %v", open)
	}
	close(release)
	if body := <-done; body != "slow" {
		t.Fatalf("body = %q", body)
	}

	// A profile's own YAML has no entries.
	if _, err := engine.Reload(directProfile(port, server.URL+"/generate_204")); err != nil {
		t.Fatal(err)
	}
	for _, rule := range engine.ListRules() {
		if rule.GetEntryId() != "" {
			t.Fatalf("rule %v names an entry", rule)
		}
	}
}

func TestInspectSourceAndGeoCategories(t *testing.T) {
	engine := openEngine(t, TunNone)
	inspection, err := engine.InspectSource([]byte(nodesSource), "剩余")
	if err != nil {
		t.Fatal(err)
	}
	if !inspection.GetHasRules() || len(inspection.GetNodes()) != 4 || !inspection.GetNodes()[3].GetInfo() {
		t.Fatalf("inspection = %v", inspection)
	}
	if _, err := engine.InspectSource([]byte("proxies: 3\n"), ""); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("broken source: %v", err)
	}
	categories, err := engine.ListGeoCategories()
	if err != nil {
		t.Fatal(err)
	}
	sites := categories.GetGeosite()
	if !slices.Equal(sites.GetNames(), []string{"cn", "openai", "private", "telegram"}) ||
		len(sites.GetSha256()) != 64 || sites.GetUpdatedAtUnixMs() == 0 {
		t.Fatalf("geosite = %v", sites)
	}
	if got := categories.GetGeoip().GetNames(); !slices.Equal(got, []string{"cn", "private", "telegram"}) {
		t.Fatalf("geoip = %v", got)
	}
}

func TestGeoUpdateDue(t *testing.T) {
	now := time.Date(2026, 10, 29, 12, 0, 0, 0, time.UTC)
	day := 24 * time.Hour
	for _, test := range []struct {
		written  time.Time
		interval time.Duration
		want     bool
	}{
		{now.Add(-day), day, true},
		{now.Add(-day + time.Minute), day, false},
		{time.Time{}, day, false}, // no data
		{now.Add(-48 * time.Hour), 0, false},
	} {
		if got := geoUpdateDue(test.written, test.interval, now); got != test.want {
			t.Errorf("written %v, interval %v: due = %v, want %v", test.written, test.interval, got, test.want)
		}
	}
}

func TestGeoUpdaterChecksAndStops(t *testing.T) {
	updates := make(chan struct{}, 8)
	updater := &geoUpdater{stop: make(chan struct{}), done: make(chan struct{})}
	stale := func() time.Time { return time.Now().Add(-1000 * time.Hour) }
	go updater.run(10*time.Millisecond, stale, func() error {
		updates <- struct{}{}
		return errors.New("offline")
	})
	for range 2 {
		select {
		case <-updates:
		case <-time.After(5 * time.Second):
			t.Fatal("the updater did not try")
		}
	}
	close(updater.stop)
	select {
	case <-updater.done:
	case <-time.After(5 * time.Second):
		t.Fatal("the updater did not stop")
	}
}

// Composed configurations start and stop the updater with the core.
func TestGeoUpdaterFollowsTheCore(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	port := freePort(t)
	plan := fmt.Sprintf(`{"version": 1, "test_url": %q, "fallback": "DIRECT"}`, server.URL)
	if _, err := engine.Start(Profile{Composition: composition(plan), Options: RunOptions{MixedPort: uint16(port)}}); err != nil {
		t.Fatal(err)
	}
	if engine.geo == nil {
		t.Fatal("no updater for geo-auto-update")
	}
	if _, err := engine.Reload(Profile{Composition: composition(plan), Options: RunOptions{MixedPort: uint16(port)},
		Overrides: [][]byte{[]byte("geo-auto-update: false\n")}}); err != nil {
		t.Fatal(err)
	}
	if engine.geo != nil {
		t.Fatal("the updater runs after geo-auto-update went off")
	}
	if _, err := engine.Reload(Profile{Composition: composition(plan), Options: RunOptions{MixedPort: uint16(port)}}); err != nil {
		t.Fatal(err)
	}
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	if engine.geo != nil {
		t.Fatal("the updater outlived the core")
	}
}
