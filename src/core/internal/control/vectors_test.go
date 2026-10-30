package control

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"os"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

// vectorEngine answers the shared vectors' requests with fixed results and keeps what it
// was asked, so the vectors stay independent of mihomo and the region table.
type vectorEngine struct {
	unlinked
	profile    *admission.Profile
	inspected  string
	infoFilter string
	preview    *compose.Composition
	groupID    string
	match      *engine.MatchRequest
	converted  string
	conversion compose.Conversion
	exported   *admission.Profile
	format     controlpb.ExportFormat
}

func (e *vectorEngine) PreviewGroup(composition compose.Composition, groupID string) (*controlpb.PreviewGroupResponse, error) {
	e.preview, e.groupID = &composition, groupID
	return &controlpb.PreviewGroupResponse{
		Nodes:  []*controlpb.PoolNode{{Name: "香港 01", OriginalName: "香港 01", SourceId: "sub-a", Type: "Shadowsocks", Region: "HK"}},
		Others: []string{"DIRECT"},
	}, nil
}

func (e *vectorEngine) MatchRule(request engine.MatchRequest) (*controlpb.MatchRuleResponse, error) {
	e.match = &request
	return &controlpb.MatchRuleResponse{
		Matched:    true,
		Index:      2,
		Rule:       &controlpb.Rule{Type: "DOMAIN-SUFFIX", Payload: "example.com", Proxy: "节点选择", EntryId: "web"},
		Chain:      []string{"香港 01", "节点选择"},
		ResolvedIp: "192.0.2.1",
	}, nil
}

func (e *vectorEngine) ConvertToPlan(source []byte, options compose.Conversion) (*controlpb.ConvertToPlanResponse, error) {
	e.converted, e.conversion = string(source), options
	return &controlpb.ConvertToPlanResponse{
		PlanJson:         `{"version":1}`,
		GroupCount:       3,
		MergedGroupCount: 1,
		RuleCount:        5,
		RawRuleCount:     1,
		EntryCount:       3,
		Skipped:          []*controlpb.SkippedRule{{Rule: "RULE-SET,ads,REJECT", Reason: "rule_set"}},
	}, nil
}

func (e *vectorEngine) ExportPlan(profile admission.Profile, format controlpb.ExportFormat) ([]byte, error) {
	e.exported, e.format = &profile, format
	return []byte("proxy-groups: []\n"), nil
}

func (e *vectorEngine) Validate(profile admission.Profile) (*controlpb.CompositionReport, error) {
	e.profile = &profile
	return &controlpb.CompositionReport{
		Warnings: []*controlpb.CompositionWarning{{EntryId: "tg", Reason: "region_empty", Target: "region:US"}},
		Groups: []*controlpb.ComposedGroup{
			{Id: "proxy", Name: "节点选择", Kind: controlpb.GroupKind_GROUP_KIND_BUILTIN},
			{Id: "region:HK", Name: "🇭🇰 香港", Kind: controlpb.GroupKind_GROUP_KIND_REGION, Region: "HK", NodeCount: 1},
		},
		Rules: []*controlpb.Rule{{Type: "GEOSITE", Payload: "telegram", Proxy: "节点选择", EntryId: "tg"}},
		Nodes: []*controlpb.PoolNode{{Name: "香港 01", OriginalName: "香港 01", SourceId: "sub-a", Type: "Shadowsocks", Region: "HK"}},
	}, nil
}

func (e *vectorEngine) InspectSource(source []byte, infoFilter string) (*controlpb.InspectSourceResponse, error) {
	e.inspected, e.infoFilter = string(source), infoFilter
	return &controlpb.InspectSourceResponse{
		Nodes:      []*controlpb.SourceNode{{Name: "剩余流量", Type: "Http", Region: "ZZ", Info: true}},
		HasRules:   true,
		GroupCount: 1,
		RuleCount:  2,
	}, nil
}

func (e *vectorEngine) ListGeoCategories() (*controlpb.ListGeoCategoriesResponse, error) {
	return &controlpb.ListGeoCategoriesResponse{
		Geosite: &controlpb.GeoCategories{
			Names:           []string{"cn", "telegram"},
			Sha256:          "ab" + strings.Repeat("0", 62),
			UpdatedAtUnixMs: 1761696000000,
		},
		Geoip: &controlpb.GeoCategories{Names: []string{"cn"}},
	}, nil
}

// TestSharedVectors answers the requests Python encodes and must produce the exact
// response bytes Python expects (testdata/control-vectors.json).
func TestSharedVectors(t *testing.T) {
	raw, err := os.ReadFile("../../../../testdata/control-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors map[string]string
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	fake := &vectorEngine{}
	dispatcher := New(Options{Engine: fake, HostMethods: true})
	for _, name := range []string{"get_info", "validate_composition", "inspect_source", "list_geo_categories",
		"preview_group", "match_rule", "convert_to_plan", "export_plan"} {
		requestFrame, err := hex.DecodeString(vectors[name+"_request_frame"])
		if err != nil {
			t.Fatal(err)
		}
		want, err := hex.DecodeString(vectors[name+"_response_frame"])
		if err != nil {
			t.Fatal(err)
		}
		payload, err := frame.Read(bytes.NewReader(requestFrame), frame.KindRequest)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		var got bytes.Buffer
		if err := frame.Write(&got, frame.KindResponse, dispatcher.Handle(context.Background(), peer.Identity{}, payload)); err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(got.Bytes(), want) {
			t.Errorf("%s: response frame = %x, want %x", name, got.Bytes(), want)
		}
	}

	wantComposition := compose.Composition{
		Sources:    []compose.Source{{ID: "sub-a", Name: "示例订阅 A", YAML: []byte("proxies: []\n")}},
		InfoFilter: "剩余|到期",
		Plan:       []byte(`{"version": 1}`),
	}
	profile := fake.profile
	if profile == nil || profile.Composition == nil || len(profile.Config) != 0 ||
		!slices.EqualFunc(profile.Overrides, [][]byte{[]byte("mode: rule\n")}, bytes.Equal) {
		t.Fatalf("validate profile = %+v", profile)
	}
	got := *profile.Composition
	if len(got.Sources) != 1 || got.Sources[0].ID != "sub-a" || got.Sources[0].Name != "示例订阅 A" ||
		!bytes.Equal(got.Sources[0].YAML, wantComposition.Sources[0].YAML) ||
		got.InfoFilter != wantComposition.InfoFilter || !bytes.Equal(got.Plan, wantComposition.Plan) {
		t.Fatalf("composition = %+v", got)
	}
	if fake.inspected != "proxies: []\n" || fake.infoFilter != "剩余" {
		t.Fatalf("inspected %q with %q", fake.inspected, fake.infoFilter)
	}
	sameComposition := func(got *compose.Composition) bool {
		return got != nil && len(got.Sources) == 1 && got.Sources[0].ID == "sub-a" &&
			bytes.Equal(got.Plan, wantComposition.Plan) && got.InfoFilter == wantComposition.InfoFilter
	}
	if !sameComposition(fake.preview) || fake.groupID != "g-1" {
		t.Fatalf("preview of %q in %+v", fake.groupID, fake.preview)
	}
	if match := fake.match; match == nil || match.Draft == nil || !sameComposition(match.Draft.Composition) ||
		len(match.Draft.Overrides) != 1 || match.Host != "example.com" || match.Port != 80 || !match.UDP {
		t.Fatalf("match = %+v", fake.match)
	}
	if conversion := fake.conversion; fake.converted != "rules: []\n" || conversion.SourceID != "sub-a" ||
		conversion.Locale != "en" || conversion.InfoFilter != "剩余" {
		t.Fatalf("converted %q with %+v", fake.converted, fake.conversion)
	}
	if exported := fake.exported; exported == nil || !sameComposition(exported.Composition) || len(exported.Overrides) != 1 ||
		exported.Options.MixedPort != 7890 || exported.Options.Stack != "gvisor" ||
		fake.format != controlpb.ExportFormat_EXPORT_FORMAT_GENERATED {
		t.Fatalf("exported %+v as %v", fake.exported, fake.format)
	}
}
