package compose

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/common/yaml"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

func nodeNames(nodes []*controlpb.PoolNode) []string {
	names := make([]string, len(nodes))
	for i, n := range nodes {
		names[i] = n.GetName()
	}
	return names
}

// The preview is the composed group: the same nodes, and the other members by id.
func TestPreviewIsTheComposedGroup(t *testing.T) {
	plan := `{"version": 1, "groups": [
		{"id": "cheap", "kind": "custom", "name": "低倍率", "members": {
		  "regions": ["HK"], "exclude": {"mode": "keyword", "pattern": "0.1X"},
		  "nodes": [{"source": "a", "name": "US-LA 0.1x"}], "others": ["auto", "region:*", "region:KR", "REJECT"]}}]}`
	composition := Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(plan)}
	preview, err := Preview(composition, "cheap")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := nodeNames(preview.GetNodes()), []string{"香港 01", "US-LA 0.1x", "香港 01 · 机场 B"}; !slices.Equal(got, want) {
		t.Errorf("nodes = %v, want %v", got, want)
	}
	// KR has no nodes, so no group.
	if got, want := preview.GetOthers(), []string{"auto", "region:HK", "region:JP", "region:US", "region:ZZ", "REJECT"}; !slices.Equal(got, want) {
		t.Errorf("others = %v, want %v", got, want)
	}
	composed := groupConfig(t, composeOK(t, plan), "低倍率")
	if !strings.Contains(composed["filter"].(string), `US-LA\ 0\.1x`) {
		t.Errorf("composed filter %v", composed["filter"])
	}

	region, err := Preview(composition, "region:JP")
	if err != nil || !slices.Equal(nodeNames(region.GetNodes()), []string{"🇯🇵 东京 02"}) {
		t.Fatalf("region preview = %v, %v", region, err)
	}
	if empty, err := Preview(composition, "region:KR"); err != nil || len(empty.GetNodes()) != 0 {
		t.Fatalf("a region without nodes = %v, %v", empty, err)
	}
	if _, err := Preview(composition, "nowhere"); err == nil {
		t.Fatal("a group the plan lacks has a preview")
	}
	broken := Composition{Plan: []byte(`{"version": 1, "groups": [{"id": "g", "kind": "custom", "name": "G",
		"members": {"name": {"mode": "regex", "pattern": "("}}}]}`)}
	if _, err := Preview(broken, "g"); err == nil || !strings.HasPrefix(err.Error(), "plan: ") {
		t.Fatalf("a broken pattern: %v", err)
	}
}

func shareYAML(t *testing.T, plan string) map[string]any {
	t.Helper()
	exported, err := Export(Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(plan)})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(exported), "proxy-groups:\n") {
		t.Fatalf("the export does not start with the groups:\n%s", exported)
	}
	for _, secret := range []string{"password", "uuid", "server", "payload", "pool:"} {
		if strings.Contains(string(exported), secret) {
			t.Fatalf("the export holds %q:\n%s", secret, exported)
		}
	}
	var doc map[string]any
	if err := yaml.Unmarshal(exported, &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func exportedGroup(t *testing.T, doc map[string]any, name string) map[string]any {
	t.Helper()
	for _, raw := range doc["proxy-groups"].([]any) {
		if group := raw.(map[string]any); group["name"] == name {
			return group
		}
	}
	t.Fatalf("no group %q", name)
	return nil
}

func TestExportWritesConditions(t *testing.T) {
	doc := shareYAML(t, `{"version": 1, "groups": [
		{"id": "cheap", "kind": "custom", "name": "低倍率", "type": "url-test", "members": {
		  "regions": ["HK", "ZZ"], "name": {"mode": "keyword", "pattern": "IPLC"},
		  "exclude": {"mode": "regex", "pattern": "0\\.1x"},
		  "nodes": [{"source": "a", "name": "US-LA 0.1x"}], "others": ["DIRECT"]},
		 "options": {"interval": 600}},
		{"id": "b-only", "kind": "custom", "name": "只用 B", "members": {"sources": ["b"]}},
		{"id": "empty", "kind": "custom", "name": "空"}],
		"rules": [{"id": "r", "items": [{"type": "DOMAIN-SUFFIX", "value": "example.com"}], "target": "cheap"}],
		"fallback": "DIRECT"}`)
	cheap := exportedGroup(t, doc, "低倍率")
	hk, _ := regionByCode("HK")
	if cheap["include-all"] != true || cheap["filter"] != hk.Pattern+"`(?i)IPLC" ||
		cheap["exclude-filter"] != `0\.1x` || cheap["interval"] != 600 || cheap["type"] != "url-test" {
		t.Errorf("低倍率 = %v", cheap)
	}
	// "Other regions" has no pattern, so Mars 1 is listed; the pick by its original name.
	if got := cheap["proxies"]; !slices.Equal(anyStrings(got), []string{"DIRECT", "US-LA 0.1x", "Mars 1"}) {
		t.Errorf("低倍率 proxies = %v", got)
	}
	// Both of B's nodes by the names B gave them.
	if got := exportedGroup(t, doc, "只用 B"); got["include-all"] != nil ||
		!slices.Equal(anyStrings(got["proxies"]), []string{"香港 01", "Mars 1"}) {
		t.Errorf("只用 B = %v", got)
	}
	if got := exportedGroup(t, doc, "空"); !slices.Equal(anyStrings(got["proxies"]), []string{"COMPATIBLE"}) {
		t.Errorf("空 = %v", got)
	}
	if got := exportedGroup(t, doc, "🇭🇰 香港"); got["filter"] != hk.Pattern || got["include-all"] != true {
		t.Errorf("香港 = %v", got)
	}
	if got := exportedGroup(t, doc, "🌐 其他地区"); !slices.Equal(anyStrings(got["proxies"]), []string{"Mars 1"}) {
		t.Errorf("其他地区 = %v", got)
	}
	if got := exportedGroup(t, doc, "自动选择"); got["include-all"] != true || got["filter"] != nil {
		t.Errorf("自动选择 = %v", got)
	}
	if got := anyStrings(doc["rules"]); !slices.Equal(got, []string{"DOMAIN-SUFFIX,example.com,低倍率", "MATCH,DIRECT"}) {
		t.Errorf("rules = %v", got)
	}
}

func anyStrings(raw any) []string {
	items, _ := raw.([]any)
	out := make([]string, len(items))
	for i, item := range items {
		out[i] = fmt.Sprint(item)
	}
	return out
}

func TestMarshalOrdered(t *testing.T) {
	out, err := MarshalOrdered(map[string]any{
		"rules":        []any{"MATCH,DIRECT"},
		"zzz":          1,
		"mode":         "rule",
		"proxy-groups": []any{map[string]any{"type": "select", "proxies": []any{"DIRECT"}, "name": "G", "a": 1}},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := "mode: rule\nproxy-groups:\n    - name: G\n      type: select\n      proxies:\n        - DIRECT\n      a: 1\nrules:\n    - MATCH,DIRECT\nzzz: 1\n"
	if string(out) != want {
		t.Fatalf("got\n%s\nwant\n%s", out, want)
	}
}

const convertible = `
proxies:
  - {name: 香港 01, type: ss, server: a.example, port: 1, cipher: aes-128-gcm, password: x}
  - {name: 香港 02, type: ss, server: a.example, port: 2, cipher: aes-128-gcm, password: x}
  - {name: 日本 01, type: ss, server: a.example, port: 3, cipher: aes-128-gcm, password: x}
  - {name: 日本 02 0.1x, type: ss, server: a.example, port: 4, cipher: aes-128-gcm, password: x}
  - {name: 剩余流量：10 GB, type: ss, server: a.example, port: 5, cipher: aes-128-gcm, password: x}
proxy-groups:
  - {name: 节点选择, type: select, proxies: [自动选择, 香港节点, 日本节点, DIRECT, 剩余流量：10 GB, 香港 01, 香港 02, 日本 01, 日本 02 0.1x]}
  - {name: 自动选择, type: url-test, url: "https://cp.cloudflare.com", interval: 300, tolerance: 50, proxies: [香港 01, 香港 02, 日本 01, 日本 02 0.1x]}
  - {name: 香港节点, type: url-test, proxies: [香港 01, 香港 02]}
  - {name: 日本节点, type: fallback, proxies: [日本 01, 日本 02 0.1x], hidden: true}
  - {name: 低倍率, type: select, proxies: [日本 02 0.1x, 香港 01]}
  - {name: "🇭🇰 香港", type: select, proxies: [香港 01]}
  - {name: Netflix, type: select, proxies: [节点选择, 香港节点, DIRECT]}
  - {name: 美国, type: load-balance, strategy: round-robin, include-all: true, filter: "(?i)美国|US", exclude-filter: "0\\.1x"}
  - {name: 全部, type: select, use: [provider-a]}
  - {name: 苹果, type: select, proxies: [DIRECT, 节点选择, 苹果]}
rules:
  - DOMAIN-SUFFIX,netflix.com,Netflix
  - DOMAIN-SUFFIX,nflxvideo.net,Netflix
  - DOMAIN,api.netflix.com,Netflix
  - DOMAIN-KEYWORD,netflix,Netflix
  - DOMAIN-SUFFIX,apple.com,苹果
  - IP-CIDR,10.0.0.1/8,DIRECT,no-resolve
  - IP-CIDR6,fc00::/7,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT
  - GEOSITE,CN,DIRECT
  - GEOIP,CN,DIRECT
  - GEOIP,telegram,节点选择,no-resolve
  - PROCESS-NAME,ssh,DIRECT
  - RULE-SET,ads,REJECT
  - SUB-RULE,(NETWORK,UDP),udp-rules
  - AND,((NETWORK,UDP),(RULE-SET,quic)),REJECT
  - OR,((DOMAIN,a.example),(DST-PORT,8443)),低倍率
  - DOMAIN-SUFFIX,unknown.example,不存在的组
  - DOMAIN-REGEX,^(broken,DIRECT
  - IP-CIDR,1.1.1.1/32,DIRECT,src
  - MATCH,节点选择
  - DOMAIN,never.example,DIRECT
`

func convertOK(t *testing.T, source string, options Conversion) (*controlpb.ConvertToPlanResponse, *Plan) {
	t.Helper()
	converted, err := Convert([]byte(source), options)
	if err != nil {
		t.Fatal(err)
	}
	plan, err := ParsePlan([]byte(converted.GetPlanJson()))
	if err != nil {
		t.Fatalf("the plan does not parse: %v\n%s", err, converted.GetPlanJson())
	}
	return converted, plan
}

func TestConvert(t *testing.T) {
	converted, plan := convertOK(t, convertible, Conversion{SourceID: "sub", InfoFilter: testInfoFilter})
	if converted.GetGroupCount() != 10 || converted.GetMergedGroupCount() != 2 || converted.GetRuleCount() != 21 ||
		converted.GetRawRuleCount() != 2 || converted.GetEntryCount() != 9 {
		t.Errorf("summary = groups %d (merged %d), rules %d (raw %d), entries %d", converted.GetGroupCount(),
			converted.GetMergedGroupCount(), converted.GetRuleCount(), converted.GetRawRuleCount(), converted.GetEntryCount())
	}
	var skipped []string
	for _, rule := range converted.GetSkipped() {
		skipped = append(skipped, rule.GetReason()+": "+rule.GetRule())
	}
	if want := []string{
		"rule_set: RULE-SET,ads,REJECT",
		"sub_rule: SUB-RULE,(NETWORK,UDP),udp-rules",
		"rule_set: AND,((NETWORK,UDP),(RULE-SET,quic)),REJECT",
		"target: DOMAIN-SUFFIX,unknown.example,不存在的组",
		"invalid: DOMAIN-REGEX,^(broken,DIRECT",
		"invalid: IP-CIDR,1.1.1.1/32,DIRECT,src",
		"after_match: DOMAIN,never.example,DIRECT",
	}; !slices.Equal(skipped, want) {
		t.Errorf("skipped = %q", skipped)
	}

	groups := map[string]PlanGroup{}
	for _, group := range plan.Groups {
		groups[group.Name] = group
	}
	if len(groups) != 8 {
		t.Fatalf("groups = %v", plan.Groups)
	}
	members := func(name string) string {
		data, _ := json.Marshal(groups[name].Members)
		return string(data)
	}
	id := func(name string) string { return groups[name].ID }
	for name, want := range map[string]string{
		// Every node of a region is that region.
		"香港节点": `{"regions":["HK"]}`,
		"日本节点": `{"regions":["JP"]}`,
		// Some nodes of two regions are picked nodes of the source.
		"低倍率": `{"nodes":[{"source":"sub","name":"日本 02 0.1x"},{"source":"sub","name":"香港 01"}]}`,
		// The region's own group name is taken.
		"🇭🇰 香港 2": `{"nodes":[{"source":"sub","name":"香港 01"}]}`,
		"Netflix":  fmt.Sprintf(`{"others":["proxy",%q,"DIRECT"]}`, id("香港节点")),
		"美国":       `{"name":{"mode":"regex","pattern":"(?i)美国|US"},"exclude":{"mode":"regex","pattern":"0\\.1x"}}`,
		"全部":       `{"all":true}`,
		// A group never lists itself.
		"苹果": `{"others":["DIRECT","proxy"]}`,
	} {
		if got := members(name); got != want {
			t.Errorf("%s members = %s, want %s", name, got, want)
		}
	}
	if g := groups["日本节点"]; g.Type != "fallback" || !g.Hidden {
		t.Errorf("日本节点 = %+v", g)
	}
	if g := groups["美国"]; g.Type != "load-balance" || g.Options.Strategy != "round-robin" {
		t.Errorf("美国 = %+v", g)
	}

	type entry struct {
		kind, target string
		items        []string
	}
	var entries []entry
	for _, rule := range plan.Rules {
		var items []string
		for _, item := range rule.Items {
			line := item.Type + "," + item.Value
			if item.NoResolve {
				line += ",no-resolve"
			}
			items = append(items, line)
		}
		entries = append(entries, entry{rule.Kind, rule.Target, items})
	}
	want := []entry{
		{"domain", id("Netflix"), []string{"DOMAIN-SUFFIX,netflix.com", "DOMAIN-SUFFIX,nflxvideo.net", "DOMAIN,api.netflix.com", "DOMAIN-KEYWORD,netflix"}},
		{"domain", id("苹果"), []string{"DOMAIN-SUFFIX,apple.com"}},
		{"ip", "DIRECT", []string{"IP-CIDR,10.0.0.0/8,no-resolve", "IP-CIDR6,fc00::/7,no-resolve"}},
		{"ip", "DIRECT", []string{"IP-CIDR,192.168.0.0/16"}},
		{"category", "DIRECT", []string{"GEOSITE,cn"}},
		{"category", "DIRECT", []string{"GEOIP,cn"}},
		{"category", "proxy", []string{"GEOIP,telegram,no-resolve"}},
		{"raw", "DIRECT", []string{"PROCESS-NAME,ssh"}},
		{"raw", id("低倍率"), []string{"OR,((DOMAIN,a.example),(DST-PORT,8443))"}},
	}
	if !slices.EqualFunc(entries, want, func(a, b entry) bool {
		return a.kind == b.kind && a.target == b.target && slices.Equal(a.items, b.items)
	}) {
		t.Errorf("entries =\n%v\nwant\n%v", entries, want)
	}
	if plan.Fallback != GroupProxy {
		t.Errorf("fallback = %q", plan.Fallback)
	}
}

func TestConvertWithoutASource(t *testing.T) {
	_, plan := convertOK(t, convertible, Conversion{InfoFilter: testInfoFilter, Locale: "en"})
	for _, group := range plan.Groups {
		if group.Name == "低倍率" {
			if got := group.Members.Name; got == nil || got.Pattern != `^(?:日本\ 02\ 0\.1x|香港\ 01)$` {
				t.Fatalf("低倍率 matches %+v", got)
			}
			return
		}
	}
	t.Fatal("no 低倍率")
}

func TestConvertRefusals(t *testing.T) {
	for name, source := range map[string]string{
		"nodes only": "proxies:\n  - {name: a, type: direct}\n",
		"not a list": "rules: 3\n",
		"links":      "ss://YWVzLTEyOC1nY206eA@a.example:1#a\n",
	} {
		if _, err := Convert([]byte(source), Conversion{}); err == nil {
			t.Errorf("%s: converted", name)
		}
	}
	if _, err := Convert([]byte(convertible), Conversion{Locale: "fr"}); err == nil {
		t.Error("an unknown locale converted")
	}
}

// GEO categories the data lacks are left out when the core knows its categories.
func TestConvertChecksCategories(t *testing.T) {
	converted, plan := convertOK(t, "rules:\n  - GEOSITE,cn,DIRECT\n  - GEOSITE,netflix,DIRECT\n  - GEOIP,CN,DIRECT\n",
		Conversion{Categories: Categories{Geosite: []string{"cn"}, Geoip: []string{"cn"}}})
	if len(plan.Rules) != 2 || len(converted.GetSkipped()) != 1 || converted.GetSkipped()[0].GetReason() != SkipInvalid {
		t.Fatalf("rules %v, skipped %v", plan.Rules, converted.GetSkipped())
	}
}

// A subscription of 400 rules: the summary adds up, and the plan composes into the rules
// the subscription had, less the skipped ones, in order and with the same targets.
func TestConvertFourHundredRules(t *testing.T) {
	var source strings.Builder
	source.WriteString(`proxies:
  - {name: 香港 01, type: http, server: a.example, port: 1}
  - {name: 日本 01, type: http, server: a.example, port: 2}
proxy-groups:
  - {name: 节点选择, type: select, proxies: [香港 01, 日本 01, DIRECT]}
  - {name: 媒体, type: select, proxies: [节点选择, 香港 01]}
  - {name: 广告, type: select, proxies: [REJECT, DIRECT]}
rules:
`)
	targets := []string{"节点选择", "媒体", "DIRECT", "广告"}
	var lines []string
	for i := range 399 {
		target := targets[i/7%len(targets)]
		var line string
		switch i % 10 {
		case 0, 1, 2:
			line = fmt.Sprintf("DOMAIN-SUFFIX,site%d.example,%s", i, target)
		case 3:
			line = fmt.Sprintf("DOMAIN-KEYWORD,word%d,%s", i, target)
		case 4:
			line = fmt.Sprintf("IP-CIDR,10.%d.0.0/16,%s,no-resolve", i%256, target)
		case 5:
			line = fmt.Sprintf("GEOSITE,site%d,%s", i, target)
		case 6:
			line = fmt.Sprintf("DST-PORT,%d,%s", 1000+i, target)
		case 7:
			line = fmt.Sprintf("RULE-SET,set%d,%s", i, target)
		case 8:
			line = fmt.Sprintf("PROCESS-NAME,app%d,%s", i, target)
		default:
			line = fmt.Sprintf("DOMAIN,host%d.example,%s", i, target)
		}
		lines = append(lines, line)
	}
	lines = append(lines, "MATCH,节点选择")
	for _, line := range lines {
		source.WriteString("  - " + line + "\n")
	}
	converted, _ := convertOK(t, source.String(), Conversion{SourceID: "sub"})
	if converted.GetRuleCount() != 400 || converted.GetGroupCount() != 3 || converted.GetMergedGroupCount() != 1 {
		t.Fatalf("summary = %v", converted)
	}
	// 40 RULE-SET rules go; DST-PORT and PROCESS-NAME, 80, stay raw.
	if len(converted.GetSkipped()) != 40 || converted.GetRawRuleCount() != 80 {
		t.Fatalf("skipped %d, raw %d", len(converted.GetSkipped()), converted.GetRawRuleCount())
	}

	result, err := Compose(Composition{
		Sources: []Source{{ID: "sub", Name: "机场", YAML: []byte(source.String())}},
		Plan:    []byte(converted.GetPlanJson()),
	})
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]string{"节点选择": "节点选择", "DIRECT": "DIRECT", "媒体": "媒体", "广告": "广告"}
	var want []string
	for _, line := range lines {
		if strings.HasPrefix(line, "RULE-SET") {
			continue
		}
		parts := strings.Split(line, ",")
		target := names[parts[2-btoi(parts[0] == "MATCH")]]
		switch {
		case parts[0] == "MATCH":
			want = append(want, "MATCH,"+target)
		case len(parts) == 4:
			want = append(want, parts[0]+","+parts[1]+","+target+","+parts[3])
		default:
			want = append(want, parts[0]+","+strings.ToLower(parts[1])+","+target)
		}
	}
	if !slices.Equal(result.Lines, want) {
		for i := range min(len(result.Lines), len(want)) {
			if result.Lines[i] != want[i] {
				t.Fatalf("rule %d = %q, want %q (%d rules, want %d)", i, result.Lines[i], want[i], len(result.Lines), len(want))
			}
		}
		t.Fatalf("%d rules, want %d", len(result.Lines), len(want))
	}
}

func btoi(b bool) int {
	if b {
		return 1
	}
	return 0
}

// An exported plan converts back into regions and conditions.
func TestExportConvertsBack(t *testing.T) {
	plan := `{"version": 1, "groups": [
		{"id": "cheap", "kind": "custom", "name": "低倍率", "members": {
		  "regions": ["HK", "JP"], "exclude": {"mode": "regex", "pattern": "0\\.1x"}, "others": ["DIRECT"]}}],
		"rules": [{"id": "r", "items": [{"type": "DOMAIN-SUFFIX", "value": "example.com"}], "target": "cheap"}]}`
	exported, err := Export(Composition{Sources: sources(), InfoFilter: testInfoFilter, Plan: []byte(plan)})
	if err != nil {
		t.Fatal(err)
	}
	_, back := convertOK(t, string(exported), Conversion{})
	for _, group := range back.Groups {
		if group.Name == "低倍率" {
			data, _ := json.Marshal(group.Members)
			if want := `{"regions":["HK","JP"],"exclude":{"mode":"regex","pattern":"0\\.1x"},"others":["DIRECT"]}`; string(data) != want {
				t.Fatalf("低倍率 = %s, want %s", data, want)
			}
			return
		}
	}
	t.Fatalf("no 低倍率 in %v", back.Groups)
}
