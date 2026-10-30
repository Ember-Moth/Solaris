package compose

import (
	"encoding/base64"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
)

const subscriptionA = `
proxies:
  - {name: 香港 01, type: ss, server: a.example, port: 1, cipher: aes-128-gcm, password: x}
  - {name: 🇯🇵 东京 02, type: trojan, server: b.example, port: 2, password: x}
  - {name: 剩余流量：10 GB, type: ss, server: c.example, port: 3, cipher: aes-128-gcm, password: x}
  - {name: US-LA 0.1x, type: vmess, server: d.example, port: 4, uuid: 2b4e5d0c-6c41-4b1e-9d5a-0a6f2f3c9e11, cipher: auto}
  - {name: 香港 01, type: ss, server: dup.example, port: 5, cipher: aes-128-gcm, password: x}
proxy-groups:
  - {name: PROXY, type: select, proxies: [香港 01]}
rules:
  - MATCH,PROXY
`

const subscriptionB = `
proxies:
  - {name: 香港 01, type: http, server: e.example, port: 6}
  - {name: Mars 1, type: http, server: f.example, port: 7}
`

const testInfoFilter = "剩余|到期|流量"

func sources() []Source {
	return []Source{
		{ID: "a", Name: "机场 A", YAML: []byte(subscriptionA)},
		{ID: "b", Name: "机场 B", YAML: []byte(subscriptionB)},
	}
}

func composeOK(t *testing.T, plan string) *Result {
	t.Helper()
	result, err := Compose(Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(plan)})
	if err != nil {
		t.Fatalf("Compose: %v", err)
	}
	return result
}

func groupConfig(t *testing.T, result *Result, name string) map[string]any {
	t.Helper()
	for _, raw := range result.Config["proxy-groups"].([]any) {
		if group := raw.(map[string]any); group["name"] == name {
			return group
		}
	}
	t.Fatalf("no group %q in %v", name, result.Config["proxy-groups"])
	return nil
}

func reportGroup(result *Result, id string) *controlpb.ComposedGroup {
	for _, group := range result.Report.Groups {
		if group.Id == id {
			return group
		}
	}
	return nil
}

func TestRegionTable(t *testing.T) {
	for name, want := range map[string]string{
		"香港 01":             "HK",
		"🇭🇰 HK-02 IPLC":     "HK",
		"HKG01":             "HK",
		"Hong Kong 03":      "HK",
		"🇨🇳 台湾 01":          "TW",
		"🇯🇵 东京 02":          "JP",
		"JP01":              "JP",
		"US-LA 0.1x":        "US",
		"美国 洛杉矶 CN2 GIA":    "US",
		"RUSSIA 1":          "RU",
		"plus 1":            OtherRegion,
		"🇷🇺 Moscow":         "RU",
		"印度尼西亚 01":          "ID",
		"印度 孟买":             "IN",
		"澳门 01":             "MO",
		"澳洲 悉尼":             "AU",
		"中国香港 01":           "HK",
		"回国 上海":             "CN",
		"CN2 GIA 香港":        "HK",
		"Mars 1":            OtherRegion,
		"Singapore Premium": "SG",
		"UK 01":             "GB",
		"剩余流量 12 GB":        OtherRegion,
		"Premium 100GB":     OtherRegion,
	} {
		if got := RegionOf(name); got != want {
			t.Errorf("RegionOf(%q) = %s, want %s", name, got, want)
		}
	}
	seen := map[string]bool{}
	for _, region := range Regions {
		if seen[region.Code] || region.Flag == "" || region.NameZH == "" || region.NameEN == "" {
			t.Errorf("region %+v repeats or misses a field", region)
		}
		seen[region.Code] = true
	}
	if last := Regions[len(Regions)-1]; last.Code != OtherRegion || last.Pattern != "" {
		t.Errorf("the table ends with %s, want the catch-all %s", last.Code, OtherRegion)
	}
}

func TestPoolDropsInfoNodesAndRenamesClashes(t *testing.T) {
	result := composeOK(t, `{"version": 1}`)
	var got []string
	for _, node := range result.Report.Nodes {
		got = append(got, node.SourceId+"/"+node.OriginalName+" → "+node.Name+" "+node.Region+" "+node.Type)
	}
	want := []string{
		"a/香港 01 → 香港 01 HK Shadowsocks",
		"a/🇯🇵 东京 02 → 🇯🇵 东京 02 JP Trojan",
		"a/US-LA 0.1x → US-LA 0.1x US Vmess",
		// The second 香港 01 of source a goes, as mihomo's provider drops it.
		"b/香港 01 → 香港 01 · 机场 B HK Http",
		"b/Mars 1 → Mars 1 ZZ Http",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("pool =\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	providers := result.Config["proxy-providers"].(map[string]any)
	payload := providers["pool:b"].(map[string]any)["payload"].([]any)
	if payload[0].(map[string]any)["name"] != "香港 01 · 机场 B" || providers["pool:a"].(map[string]any)["type"] != "inline" {
		t.Fatalf("providers = %v", providers)
	}
}

func TestDefaultGroups(t *testing.T) {
	result := composeOK(t, `{"version": 1}`)
	var names []string
	for _, group := range result.Report.Groups {
		names = append(names, group.Id+"="+group.Name)
	}
	want := []string{"proxy=节点选择", "auto=自动选择", "region:HK=🇭🇰 香港", "region:JP=🇯🇵 日本",
		"region:US=🇺🇸 美国", "region:ZZ=🌐 其他地区"}
	if !slices.Equal(names, want) {
		t.Fatalf("groups = %v, want %v", names, want)
	}
	proxy := groupConfig(t, result, "节点选择")
	if proxy["type"] != "select" || !slices.Equal(proxy["proxies"].([]string),
		[]string{"自动选择", "🇭🇰 香港", "🇯🇵 日本", "🇺🇸 美国", "🌐 其他地区", "DIRECT"}) || proxy["use"] != nil {
		t.Fatalf("节点选择 = %v", proxy)
	}
	// Every node: the providers, no filter.
	auto := groupConfig(t, result, "自动选择")
	if auto["type"] != "url-test" || auto["filter"] != nil || len(auto["use"].([]any)) != 2 {
		t.Fatalf("自动选择 = %v", auto)
	}
	hk := groupConfig(t, result, "🇭🇰 香港")
	if hk["type"] != "url-test" || hk["filter"] != `^(?:香港\ 01|香港\ 01\ ·\ 机场\ B)$` ||
		!slices.Equal(hk["use"].([]any), []any{"pool:a", "pool:b"}) {
		t.Fatalf("🇭🇰 香港 = %v", hk)
	}
	if got := reportGroup(result, "region:HK"); got.Kind != controlpb.GroupKind_GROUP_KIND_REGION || got.Region != "HK" || got.NodeCount != 2 {
		t.Fatalf("report of region:HK = %v", got)
	}
}

func TestEnglishNamesAndRegionSettings(t *testing.T) {
	result := composeOK(t, `{"version": 1, "locale": "en", "groups": [
		{"id": "region:JP", "kind": "region", "type": "select", "hidden": true},
		{"id": "proxy", "kind": "builtin", "name": "Main"}]}`)
	jp := groupConfig(t, result, "🇯🇵 Japan")
	if jp["type"] != "select" || jp["hidden"] != true {
		t.Fatalf("Japan = %v", jp)
	}
	groupConfig(t, result, "Auto")
	if proxy := groupConfig(t, result, "Main"); proxy["proxies"].([]string)[0] != "Auto" {
		t.Fatalf("Main = %v", proxy)
	}
}

// docs/routing.md §4.5: regions, sources and names add up; the exclusion takes away; the
// picked nodes and the other members join regardless.
func TestCustomGroupMembers(t *testing.T) {
	result := composeOK(t, `{"version": 1, "groups": [
		{"id": "g", "kind": "custom", "name": "我的组", "type": "fallback",
		 "members": {"regions": ["JP"], "sources": ["b"], "name": {"mode": "keyword", "pattern": "us-la"},
		             "exclude": {"mode": "regex", "pattern": "0\\.1x|Mars"},
		             "nodes": [{"source": "b", "name": "Mars 1"}, {"source": "a", "name": "gone"}],
		             "others": ["auto", "region:US", "region:KR", "DIRECT"]},
		 "options": {"url": "http://127.0.0.1/t", "interval": 60, "tolerance": 30, "lazy": false}}]}`)
	group := groupConfig(t, result, "我的组")
	// JP from a; everything from b less Mars, which the pick brings back; US-LA is excluded.
	if group["filter"] != `^(?:🇯🇵\ 东京\ 02|香港\ 01\ ·\ 机场\ B|Mars\ 1)$` {
		t.Errorf("filter = %v", group["filter"])
	}
	// No KR nodes, so no KR group to join.
	if !slices.Equal(group["proxies"].([]string), []string{"自动选择", "🇺🇸 美国", "DIRECT"}) {
		t.Errorf("proxies = %v", group["proxies"])
	}
	if group["type"] != "fallback" || group["url"] != "http://127.0.0.1/t" || group["interval"] != 60 ||
		group["lazy"] != false || group["tolerance"] != nil {
		t.Errorf("group = %v", group)
	}
	if got := reportGroup(result, "g"); got.NodeCount != 3 || got.Kind != controlpb.GroupKind_GROUP_KIND_CUSTOM {
		t.Errorf("report = %v", got)
	}
}

func TestRulesExpandInOrder(t *testing.T) {
	result := composeOK(t, `{"version": 1, "test_url": "http://127.0.0.1/204",
		"groups": [{"id": "empty", "kind": "custom", "name": "空组", "members": {"regions": ["KR"]}}],
		"rules": [
		{"id": "off", "kind": "domain", "enabled": false, "items": [{"type": "DOMAIN", "value": "x.example"}], "target": "REJECT"},
		{"id": "tg", "kind": "service", "items": [{"type": "geosite", "value": "telegram"},
		  {"type": "GEOIP", "value": "telegram", "no_resolve": true}], "target": "region:JP"},
		{"id": "kr", "items": [{"type": "DOMAIN-SUFFIX", "value": "kr.example"}], "target": "region:KR"},
		{"id": "e", "items": [{"type": "AND", "value": "((DOMAIN,a.example),(NETWORK,UDP))"}], "target": "empty"},
		{"id": "lan", "items": [{"type": "IP-CIDR", "value": "192.168.0.0/16", "no_resolve": true}], "target": "DIRECT"}],
		"fallback": "auto"}`)
	want := []string{
		"GEOSITE,telegram,🇯🇵 日本",
		"GEOIP,telegram,🇯🇵 日本,no-resolve",
		"DOMAIN-SUFFIX,kr.example,节点选择",
		"AND,((DOMAIN,a.example),(NETWORK,UDP)),节点选择",
		"IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
		"MATCH,自动选择",
	}
	rules := result.Config["rules"].([]any)
	if len(rules) != len(want) || !slices.Equal(result.Lines, want) {
		t.Fatalf("rules = %v, want %v", rules, want)
	}
	var entries []string
	for _, rule := range result.Report.Rules {
		entries = append(entries, rule.EntryId)
	}
	if !slices.Equal(entries, []string{"tg", "tg", "kr", "e", "lan", FallbackEntry}) {
		t.Fatalf("entries = %v", entries)
	}
	var warnings []string
	for _, warning := range result.Report.Warnings {
		warnings = append(warnings, warning.EntryId+" "+warning.Reason+" "+warning.Target)
	}
	if !slices.Equal(warnings, []string{"kr region_empty region:KR", "e group_empty empty"}) {
		t.Fatalf("warnings = %v", warnings)
	}
	// A group without members holds mihomo's stand-in, as mihomo would.
	if empty := groupConfig(t, result, "空组"); !slices.Equal(empty["proxies"].([]any), []any{"COMPATIBLE"}) ||
		empty["url"] != "http://127.0.0.1/204" {
		t.Fatalf("空组 = %v", empty)
	}
}

func TestEmptyPool(t *testing.T) {
	result, err := Compose(Composition{Plan: []byte(`{"version": 1, "rules": [
		{"id": "x", "items": [{"type": "DOMAIN", "value": "a.example"}], "target": "auto"}]}`)})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Config["proxy-providers"].(map[string]any)) != 0 {
		t.Fatalf("providers = %v", result.Config["proxy-providers"])
	}
	auto := groupConfig(t, result, "自动选择")
	if !slices.Equal(auto["proxies"].([]any), []any{"COMPATIBLE"}) || auto["use"] != nil {
		t.Fatalf("自动选择 = %v", auto)
	}
	if len(result.Report.Warnings) != 1 || result.Report.Warnings[0].Reason != ReasonGroupEmpty {
		t.Fatalf("warnings = %v", result.Report.Warnings)
	}
}

func TestBaseSection(t *testing.T) {
	config := composeOK(t, `{"version": 1}`).Config
	dns := config["dns"].(map[string]any)
	if config["geodata-mode"] != true || config["geo-auto-update"] != true || dns["enhanced-mode"] != "fake-ip" ||
		dns["proxy-server-nameserver"] == nil || config["sniffer"].(map[string]any)["enable"] != true {
		t.Fatalf("base section = %v", config)
	}
	if _, ok := config["proxies"]; ok {
		t.Fatal("the nodes belong in the providers")
	}
}

func TestPlanErrors(t *testing.T) {
	for plan, want := range map[string]string{
		``:                                   "empty",
		`{"version": 2}`:                     "version 2",
		`{"version": 1, "locale": "fr"}`:     "locale",
		`{"version": 1, "fallback": "nope"}`: "unknown target",
		`{"version": 1, "fallback": "region:XX"}`:                                                                                        `unknown region "XX"`,
		`{"version": 1, "groups": [{"id": "g", "kind": "custom"}]}`:                                                                      "needs a name",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "a,b"}]}`:                                                       "comma",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "DIRECT"}]}`:                                                    "mihomo's own",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "自动选择"}]}`:                                                      "taken",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G", "type": "relay"}]}`:                                        "unknown type",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G", "members": {"others": ["g"]}}]}`:                           "its own member",
		`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G", "members": {"name": {"mode": "regex", "pattern": "("}}}]}`: "name pattern",
		`{"version": 1, "groups": [{"id": "region:HK", "kind": "custom", "name": "G"}]}`:                                                 "an id of its own",
		`{"version": 1, "groups": [{"id": "x", "kind": "builtin"}]}`:                                                                     "built-in groups",
		`{"version": 1, "rules": [{"id": "r", "items": [], "target": "DIRECT"}]}`:                                                        "no items",
		`{"version": 1, "rules": [{"id": "fallback", "items": [{"type": "DOMAIN", "value": "a"}], "target": "DIRECT"}]}`:                 "an id other than",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "MATCH", "value": "a"}], "target": "DIRECT"}]}`:                         "unknown rule type",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "RULE-SET", "value": "a"}], "target": "DIRECT"}]}`:                      "unknown rule type",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "DOMAIN", "value": "a,b"}], "target": "DIRECT"}]}`:                      "comma",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "DOMAIN", "value": "a", "no_resolve": true}], "target": "DIRECT"}]}`:    "no_resolve",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "DOMAIN", "value": "a"}], "target": "region:*"}]}`:                      "not a target",
		`{"version": 1, "rules": [{"id": "r", "items": [{"type": "DOMAIN", "value": "a"}], "target": "DIRECT"},
		  {"id": "r", "items": [{"type": "DOMAIN", "value": "b"}], "target": "DIRECT"}]}`: "twice",
	} {
		_, err := Compose(Composition{Plan: []byte(plan)})
		if err == nil || !strings.Contains(err.Error(), want) || !strings.HasPrefix(err.Error(), "plan: ") {
			t.Errorf("plan %s: error %v, want one about %q", plan, err, want)
		}
	}
}

func TestSourceErrors(t *testing.T) {
	plan := []byte(`{"version": 1}`)
	for name, composition := range map[string]Composition{
		"bad YAML":    {Sources: []Source{{ID: "a", Name: "A", YAML: []byte("proxies: [\n")}}, Plan: plan},
		"proxies":     {Sources: []Source{{ID: "a", Name: "A", YAML: []byte("proxies: 3\n")}}, Plan: plan},
		"no id":       {Sources: []Source{{Name: "A", YAML: []byte(subscriptionB)}}, Plan: plan},
		"same id":     {Sources: []Source{{ID: "a", Name: "A"}, {ID: "a", Name: "B"}}, Plan: plan},
		"info filter": {InfoFilter: "(", Plan: plan},
	} {
		if _, err := Compose(composition); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
}

func TestInspect(t *testing.T) {
	inspection, err := Inspect([]byte(subscriptionA), testInfoFilter)
	if err != nil {
		t.Fatal(err)
	}
	if !inspection.HasRules || inspection.GroupCount != 1 || inspection.RuleCount != 1 || len(inspection.Nodes) != 4 {
		t.Fatalf("inspection = %v", inspection)
	}
	if node := inspection.Nodes[2]; node.Name != "剩余流量：10 GB" || !node.Info || node.Type != "Shadowsocks" {
		t.Fatalf("info node = %v", node)
	}
	// Share links, base64-encoded as subscriptions serve them.
	links := "trojan://secret@t.example:443#%F0%9F%87%B8%F0%9F%87%AC%20SG%2001\n" +
		"ss://YWVzLTEyOC1nY206eA@s.example:8388#Tokyo\n"
	inspection, err = Inspect([]byte(base64.StdEncoding.EncodeToString([]byte(links))), "")
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, node := range inspection.Nodes {
		got = append(got, node.Name+" "+node.Type+" "+node.Region)
	}
	if inspection.HasRules || !slices.Equal(got, []string{"🇸🇬 SG 01 Trojan SG", "Tokyo Shadowsocks JP"}) {
		t.Fatalf("share links = %v (%v)", got, inspection)
	}
	if _, err := Inspect([]byte("   "), ""); err == nil {
		t.Fatal("an empty subscription inspected fine")
	}
}
