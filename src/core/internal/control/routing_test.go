package control

import (
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const poolSource = "proxies:\n  - {name: 🇯🇵 东京 01, type: direct}\n  - {name: 剩余流量, type: direct}\n"

func compositionOf(plan string) *controlpb.Composition {
	return &controlpb.Composition{
		Sources:    []*controlpb.CompositionSource{{Id: "a", Name: "A", Yaml: poolSource}},
		InfoFilter: "剩余",
		PlanJson:   plan,
	}
}

// Validate, Start and Reload answer a composition with its report, and a profile's own
// YAML with Empty, as before.
func TestCompositionsAnswerWithTheirReport(t *testing.T) {
	dispatcher := development(t)
	port := freePort(t)
	plan := `{"version": 1, "test_url": "http://127.0.0.1:1/", "rules": [
		{"id": "us", "items": [{"type": "DOMAIN", "value": "us.example"}], "target": "region:US"}]}`
	options := &controlpb.RunOptions{MixedPort: uint32(port)}

	validated := call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{
			Source: &controlpb.ValidateRequest_Composition{Composition: compositionOf(plan)},
		}}
	}))
	report := validated.GetComposition()
	if report == nil {
		t.Fatalf("validate = %v", validated)
	}
	var groups []string
	for _, group := range report.GetGroups() {
		groups = append(groups, group.GetId())
	}
	if !slices.Equal(groups, []string{"proxy", "auto", "region:JP"}) || len(report.GetNodes()) != 1 {
		t.Fatalf("report = %v", report)
	}
	if warnings := report.GetWarnings(); len(warnings) != 1 || warnings[0].GetReason() != "region_empty" {
		t.Fatalf("warnings = %v", warnings)
	}

	started := call(t, dispatcher, request(2, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Start{Start: &controlpb.StartRequest{
			Source:  &controlpb.StartRequest_Composition{Composition: compositionOf(plan)},
			Options: options,
		}}
	}))
	if started.GetComposition() == nil {
		t.Fatalf("start = %v", started)
	}
	reloaded := call(t, dispatcher, request(3, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Reload{Reload: &controlpb.ReloadRequest{
			Source:  &controlpb.ReloadRequest_Composition{Composition: compositionOf(`{"version": 1}`)},
			Options: options,
		}}
	}))
	if reloaded.GetComposition() == nil || len(reloaded.GetComposition().GetWarnings()) != 0 {
		t.Fatalf("reload = %v", reloaded)
	}
	if plain := call(t, dispatcher, request(4, reloadRequest(profileYAML(port), nil))); plain.GetEmpty() == nil {
		t.Fatalf("reload with config_yaml = %v", plain)
	}

	broken := call(t, dispatcher, request(5, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Reload{Reload: &controlpb.ReloadRequest{
			Source:  &controlpb.ReloadRequest_Composition{Composition: compositionOf(`{"version": 1, "fallback": "nowhere"}`)},
			Options: options,
		}}
	}))
	expectError(t, broken, 5, CodeInvalidConfig)
	if message := broken.GetError().GetMessage(); message != `plan: fallback: unknown target "nowhere"` {
		t.Errorf("message = %q", message)
	}
}

// InspectSource and ListGeoCategories need no running core, and the macOS extension
// serves them too.
func TestRoutingMethodsOnEveryHost(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: core, HostMethods: host})
		inspected := call(t, dispatcher, request(1, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_InspectSource{InspectSource: &controlpb.InspectSourceRequest{
				Yaml: poolSource, InfoFilter: "剩余",
			}}
		})).GetInspectSource()
		nodes := inspected.GetNodes()
		if len(nodes) != 2 || nodes[0].GetRegion() != "JP" || nodes[0].GetType() != "Direct" || !nodes[1].GetInfo() || inspected.GetHasRules() {
			t.Fatalf("host=%v inspection = %v", host, inspected)
		}
		broken := call(t, dispatcher, request(2, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_InspectSource{InspectSource: &controlpb.InspectSourceRequest{Yaml: "proxies: 3\n"}}
		}))
		expectError(t, broken, 2, CodeInvalidConfig)
		categories := call(t, dispatcher, request(3, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_ListGeoCategories{ListGeoCategories: &controlpb.ListGeoCategoriesRequest{}}
		})).GetListGeoCategories()
		if !slices.Contains(categories.GetGeosite().GetNames(), "telegram") || !slices.Contains(categories.GetGeoip().GetNames(), "cn") {
			t.Fatalf("host=%v categories = %v", host, categories)
		}
		dispatcher.Close()
	}
}

// The routing editor's methods need no running core and run on every host; malformed
// requests are invalid_request, drafts the core refuses invalid_config.
func TestPlanMethodsOnEveryHost(t *testing.T) {
	plan := `{"version": 1, "groups": [{"id": "jp", "kind": "custom", "name": "日本", "members": {"regions": ["JP"], "others": ["DIRECT"]}}],
		"rules": [{"id": "web", "items": [{"type": "DOMAIN-SUFFIX", "value": "example.com"}], "target": "jp"}]}`
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: core, HostMethods: host})
		preview := call(t, dispatcher, request(1, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_PreviewGroup{PreviewGroup: &controlpb.PreviewGroupRequest{
				Composition: compositionOf(plan), GroupId: "jp",
			}}
		})).GetPreviewGroup()
		if len(preview.GetNodes()) != 1 || !slices.Equal(preview.GetOthers(), []string{"DIRECT"}) {
			t.Fatalf("host=%v preview = %v", host, preview)
		}

		matched := call(t, dispatcher, request(2, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
				Source: &controlpb.MatchRuleRequest_Composition{Composition: compositionOf(plan)},
				Host:   "www.example.com",
			}}
		})).GetMatchRule()
		if !matched.GetMatched() || matched.GetRule().GetEntryId() != "web" || !slices.Equal(matched.GetChain(), []string{"日本"}) {
			t.Fatalf("host=%v match = %v", host, matched)
		}
		profileMatch := call(t, dispatcher, request(3, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
				Source:  &controlpb.MatchRuleRequest_ConfigYaml{ConfigYaml: "rules: ['DOMAIN-KEYWORD,example,REJECT']\n"},
				Host:    "example.org",
				Port:    80,
				Network: "udp",
			}}
		})).GetMatchRule()
		if profileMatch.GetRule().GetType() != "DOMAIN-KEYWORD" || profileMatch.GetRule().GetEntryId() != "" {
			t.Fatalf("host=%v match on a profile = %v", host, profileMatch)
		}

		converted := call(t, dispatcher, request(4, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_ConvertToPlan{ConvertToPlan: &controlpb.ConvertToPlanRequest{
				Yaml: poolSource + "rules:\n  - DOMAIN,a.example,DIRECT\n  - MATCH,REJECT\n", Locale: "en",
			}}
		})).GetConvertToPlan()
		if converted.GetRuleCount() != 2 || converted.GetEntryCount() != 1 ||
			converted.GetPlanJson() != `{"version":1,"locale":"en","rules":[{"id":"r-b04584560472","kind":"domain","items":[{"type":"DOMAIN","value":"a.example"}],"target":"DIRECT"}],"fallback":"REJECT"}` {
			t.Fatalf("host=%v conversion = %v", host, converted)
		}

		for format, prefix := range map[controlpb.ExportFormat]string{
			controlpb.ExportFormat_EXPORT_FORMAT_SHARE:     "proxy-groups:\n",
			controlpb.ExportFormat_EXPORT_FORMAT_GENERATED: "mode: rule\n",
		} {
			exported := call(t, dispatcher, request(5, func(r *controlpb.Request) {
				r.Method = &controlpb.Request_ExportPlan{ExportPlan: &controlpb.ExportPlanRequest{
					Composition: compositionOf(plan), Format: format,
				}}
			})).GetExportPlan()
			if !strings.HasPrefix(exported.GetYaml(), prefix) {
				t.Fatalf("host=%v %v export = %q", host, format, exported.GetYaml())
			}
		}

		for id, method := range map[uint64]func(*controlpb.Request){
			10: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_PreviewGroup{PreviewGroup: &controlpb.PreviewGroupRequest{GroupId: "jp"}}
			},
			11: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
					Source: &controlpb.MatchRuleRequest_Composition{Composition: compositionOf(plan)},
					Host:   "example.com", Network: "sctp",
				}}
			},
			12: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
					Source: &controlpb.MatchRuleRequest_Composition{Composition: compositionOf(plan)},
					Host:   "example.com", Port: 70000,
				}}
			},
			13: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_ExportPlan{ExportPlan: &controlpb.ExportPlanRequest{}}
			},
			14: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_ExportPlan{ExportPlan: &controlpb.ExportPlanRequest{
					Composition: compositionOf(plan), Format: 9,
				}}
			},
			15: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
					Source: &controlpb.MatchRuleRequest_Composition{Composition: compositionOf(plan)},
					Host:   "not a host",
				}}
			},
		} {
			expectError(t, call(t, dispatcher, request(id, method)), id, CodeInvalidRequest)
		}
		for id, method := range map[uint64]func(*controlpb.Request){
			20: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_PreviewGroup{PreviewGroup: &controlpb.PreviewGroupRequest{
					Composition: compositionOf(`{"version": 1}`), GroupId: "nowhere",
				}}
			},
			21: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_ConvertToPlan{ConvertToPlan: &controlpb.ConvertToPlanRequest{Yaml: poolSource}}
			},
			22: func(r *controlpb.Request) {
				r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{
					Source: &controlpb.MatchRuleRequest_Composition{Composition: compositionOf(`{"version": 3}`)},
					Host:   "example.com",
				}}
			},
		} {
			expectError(t, call(t, dispatcher, request(id, method)), id, CodeInvalidConfig)
		}
		// Without a source the running configuration answers, and the core is stopped.
		expectError(t, call(t, dispatcher, request(30, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_MatchRule{MatchRule: &controlpb.MatchRuleRequest{Host: "example.com"}}
		})), 30, CodeNotRunning)
		dispatcher.Close()
	}
}
