// Package compose assembles a mihomo configuration from the node pool and a plan, for
// "my plan" routing (docs/routing.md §8). The result goes through the engine's admission
// pipeline like any profile: the overrides, the platform patches and the security items
// still apply after it.
package compose

import (
	"cmp"
	_ "embed"
	"fmt"
	"slices"
	"strings"

	"github.com/dlclark/regexp2"
	"github.com/metacubex/mihomo/common/yaml"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

//go:embed base.yaml
var baseYAML []byte

// Composition is what Validate, Start and Reload carry instead of a profile.
type Composition struct {
	Sources []Source
	// InfoFilter matches the names of information nodes, which stay out of the pool.
	InfoFilter string
	Plan       []byte
}

// Result is a composed configuration with its report. Lines[i] is the rule
// Report.Rules[i] describes, as the configuration writes it.
type Result struct {
	Config map[string]any
	Report *controlpb.CompositionReport
	Lines  []string
}

const (
	ReasonRegionEmpty = "region_empty"
	ReasonGroupEmpty  = "group_empty"

	providerPrefix = "pool:"
	// compatible is mihomo's stand-in for a group without members; it rejects.
	compatible = "COMPATIBLE"
)

// group is a group of the generated configuration.
type group struct {
	id     string
	name   string
	kind   controlpb.GroupKind
	region string
	plan   *PlanGroup
	nodes  []*node
	others []string // names of the group's other members
}

func (g *group) empty() bool { return len(g.nodes) == 0 && len(g.others) == 0 }

type composer struct {
	plan    *Plan
	sources []Source
	pool    []*node
	groups  []*group
	byID    map[string]*group
}

// Compose assembles the configuration: the base section, one inline provider per
// source, the groups and the rules.
func Compose(composition Composition) (*Result, error) {
	plan, err := ParsePlan(composition.Plan)
	if err != nil {
		return nil, fmt.Errorf("plan: %w", err)
	}
	filter, err := newInfoFilter(composition.InfoFilter)
	if err != nil {
		return nil, err
	}
	if err := checkSources(composition.Sources); err != nil {
		return nil, err
	}
	c := &composer{plan: plan, sources: composition.Sources, byID: map[string]*group{}}
	taken, err := c.groupNames()
	if err != nil {
		return nil, fmt.Errorf("plan: %w", err)
	}
	if c.pool, err = buildPool(composition.Sources, filter, taken); err != nil {
		return nil, err
	}
	c.layOutGroups()
	for _, g := range c.groups {
		if g.kind != controlpb.GroupKind_GROUP_KIND_REGION {
			c.resolveMembers(g)
		}
	}
	var config map[string]any
	if err := yaml.Unmarshal(baseYAML, &config); err != nil {
		return nil, fmt.Errorf("base section: %w", err)
	}
	result := &Result{Config: config, Report: &controlpb.CompositionReport{}}
	config["proxy-providers"] = c.providers()
	config["proxy-groups"] = c.groupConfigs(result.Report)
	config["rules"] = c.rules(result)
	for _, n := range c.pool {
		result.Report.Nodes = append(result.Report.Nodes, &controlpb.PoolNode{
			Name:         n.name,
			OriginalName: n.original,
			SourceId:     c.sources[n.source].ID,
			Type:         n.kind,
			Region:       n.region,
		})
	}
	return result, nil
}

func checkSources(sources []Source) error {
	seen := map[string]bool{}
	for _, source := range sources {
		switch {
		case source.ID == "":
			return fmt.Errorf("subscription %q has no id", source.Name)
		case seen[source.ID]:
			return fmt.Errorf("subscription id %q appears twice", source.ID)
		case strings.TrimSpace(source.Name) == "":
			return fmt.Errorf("subscription %q has no name", source.ID)
		}
		seen[source.ID] = true
	}
	return nil
}

// builtinName is a built-in group's name when the plan gives none.
func builtinName(id, locale string) string {
	names := map[string][2]string{GroupProxy: {"节点选择", "Proxy"}, GroupAuto: {"自动选择", "Auto"}}
	if locale == localeEN {
		return names[id][1]
	}
	return names[id][0]
}

func regionCode(id string) string { return strings.TrimPrefix(id, regionPrefix) }

// groupNames settles every name a group may take, before the pool takes node names: the
// built-in and custom groups, and every region of the table whether it has nodes or not.
// Names must be unique, and differ from the providers'.
func (c *composer) groupNames() (map[string]bool, error) {
	taken := map[string]bool{}
	for _, name := range reservedNames {
		taken[name] = true
	}
	claim := func(id, name string) error {
		if taken[name] {
			return fmt.Errorf("group %q: the name %q is taken", id, name)
		}
		if strings.HasPrefix(name, providerPrefix) {
			return fmt.Errorf("group %q: names starting with %q are the subscriptions'", id, providerPrefix)
		}
		taken[name] = true
		return nil
	}
	for _, id := range []string{GroupProxy, GroupAuto} {
		if err := claim(id, c.name(id)); err != nil {
			return nil, err
		}
	}
	for _, region := range Regions {
		id := regionPrefix + region.Code
		if err := claim(id, c.name(id)); err != nil {
			return nil, err
		}
	}
	for i := range c.plan.Groups {
		if planned := &c.plan.Groups[i]; planned.Kind == kindCustom {
			if err := claim(planned.ID, planned.Name); err != nil {
				return nil, err
			}
		}
	}
	return taken, nil
}

// name is the generated name of the built-in or region group id.
func (c *composer) name(id string) string {
	if planned := c.plan.group(id); planned != nil && planned.Name != "" {
		return planned.Name
	}
	if strings.HasPrefix(id, regionPrefix) {
		region, _ := regionByCode(regionCode(id))
		return region.groupName(c.plan.Locale)
	}
	return builtinName(id, c.plan.Locale)
}

// layOutGroups orders the groups as the configuration lists them: the built-in groups, the
// regions that have nodes in table order, then the custom groups in plan order.
func (c *composer) layOutGroups() {
	add := func(g *group) {
		c.groups = append(c.groups, g)
		c.byID[g.id] = g
	}
	defaults := map[string]*PlanGroup{
		GroupProxy: {ID: GroupProxy, Kind: kindBuiltin, Type: "select",
			Members: &Members{Others: []string{GroupAuto, allRegions, "DIRECT"}}},
		GroupAuto: {ID: GroupAuto, Kind: kindBuiltin, Type: "url-test", Members: &Members{All: true}},
	}
	for _, id := range []string{GroupProxy, GroupAuto} {
		planned := c.plan.group(id)
		if planned == nil {
			planned = defaults[id]
		}
		if planned.Type == "" {
			planned.Type = defaults[id].Type
		}
		if planned.Members == nil {
			planned.Members = defaults[id].Members
		}
		add(&group{id: id, name: c.name(id), kind: controlpb.GroupKind_GROUP_KIND_BUILTIN, plan: planned})
	}
	for _, region := range Regions {
		var nodes []*node
		for _, n := range c.pool {
			if n.region == region.Code {
				nodes = append(nodes, n)
			}
		}
		if len(nodes) == 0 {
			continue
		}
		id := regionPrefix + region.Code
		planned := c.plan.group(id)
		if planned == nil {
			planned = &PlanGroup{ID: id, Kind: kindRegion}
		}
		if planned.Type == "" {
			planned.Type = "url-test"
		}
		add(&group{id: id, name: c.name(id), kind: controlpb.GroupKind_GROUP_KIND_REGION,
			region: region.Code, plan: planned, nodes: nodes})
	}
	for i := range c.plan.Groups {
		planned := &c.plan.Groups[i]
		if planned.Kind != kindCustom {
			continue
		}
		if planned.Type == "" {
			planned.Type = "select"
		}
		if planned.Members == nil {
			planned.Members = &Members{}
		}
		add(&group{id: planned.ID, name: planned.Name, kind: controlpb.GroupKind_GROUP_KIND_CUSTOM, plan: planned})
	}
}

// resolveMembers applies docs/routing.md §4.5: regions, sources and the name match add
// up, the exclusion takes away, and the picked nodes and other members join regardless.
func (c *composer) resolveMembers(g *group) {
	members := g.plan.Members
	chosen := make([]bool, len(c.pool))
	for i, n := range c.pool {
		source := c.sources[n.source].ID
		if members.All || slices.Contains(members.Regions, n.region) ||
			slices.Contains(members.Sources, source) ||
			(members.Name != nil && members.Name.matches(n.original)) {
			chosen[i] = !(members.Exclude != nil && members.Exclude.matches(n.original))
		}
	}
	for _, ref := range members.Nodes {
		for i, n := range c.pool {
			if c.sources[n.source].ID == ref.Source && n.original == ref.Name {
				chosen[i] = true
			}
		}
	}
	for i, n := range c.pool {
		if chosen[i] {
			g.nodes = append(g.nodes, n)
		}
	}
	add := func(name string) {
		if !slices.Contains(g.others, name) {
			g.others = append(g.others, name)
		}
	}
	for _, other := range members.Others {
		switch {
		case other == allRegions:
			for _, candidate := range c.groups {
				if candidate.kind == controlpb.GroupKind_GROUP_KIND_REGION {
					add(candidate.name)
				}
			}
		case slices.Contains(outbounds, other):
			add(other)
		default:
			// A region without nodes has no group to join.
			if member, ok := c.byID[other]; ok {
				add(member.name)
			}
		}
	}
}

func providerName(source Source) string { return providerPrefix + source.ID }

// providers turns each source with nodes into an inline provider.
func (c *composer) providers() map[string]any {
	providers := map[string]any{}
	for index, source := range c.sources {
		var payload []any
		for _, n := range c.pool {
			if n.source == index {
				payload = append(payload, n.mapping)
			}
		}
		if len(payload) != 0 {
			providers[providerName(source)] = map[string]any{"type": "inline", "payload": payload}
		}
	}
	return providers
}

// exactFilter matches exactly the given names, as mihomo's group filter reads it: the
// backtick separates filters, so it is spelled out as \x60.
func exactFilter(nodes []*node) string {
	names := make([]string, len(nodes))
	for i, n := range nodes {
		names[i] = strings.ReplaceAll(regexp2.Escape(n.name), "`", `\x60`)
	}
	return "^(?:" + strings.Join(names, "|") + ")$"
}

func (c *composer) groupConfigs(report *controlpb.CompositionReport) []any {
	configs := make([]any, 0, len(c.groups))
	for _, g := range c.groups {
		config := map[string]any{"name": g.name, "type": g.plan.Type}
		if len(g.others) != 0 {
			config["proxies"] = slices.Clone(g.others)
		}
		if len(g.nodes) != 0 {
			var use []any
			for index, source := range c.sources {
				if slices.ContainsFunc(g.nodes, func(n *node) bool { return n.source == index }) {
					use = append(use, providerName(source))
				}
			}
			config["use"] = use
			// Members are names, so the generated configuration holds what the plan computed;
			// a filter that mihomo evaluated on its own could not leave out the exclusions
			// while keeping the picked nodes (docs/routing.md §8.5).
			if len(g.nodes) != len(c.pool) {
				config["filter"] = exactFilter(g.nodes)
			}
		}
		if g.empty() {
			config["proxies"] = []any{compatible}
		}
		if g.plan.Hidden {
			config["hidden"] = true
		}
		c.applyOptions(config, g.plan)
		configs = append(configs, config)
		composed := &controlpb.ComposedGroup{
			Id:        g.id,
			Name:      g.name,
			Kind:      g.kind,
			Region:    g.region,
			NodeCount: uint32(len(g.nodes)),
		}
		report.Groups = append(report.Groups, composed)
	}
	return configs
}

func (c *composer) applyOptions(config map[string]any, planned *PlanGroup) {
	options := planned.Options
	if url := cmp.Or(options.URL, c.plan.TestURL); url != "" {
		config["url"] = url
	}
	if options.Interval > 0 {
		config["interval"] = options.Interval
	}
	if options.Tolerance > 0 && planned.Type == "url-test" {
		config["tolerance"] = options.Tolerance
	}
	if options.Timeout > 0 {
		config["timeout"] = options.Timeout
	}
	if options.Lazy != nil {
		config["lazy"] = *options.Lazy
	}
	if options.Strategy != "" && planned.Type == "load-balance" {
		config["strategy"] = options.Strategy
	}
}

// target is the name a rule entry's target has in the configuration. A region without
// nodes or a group without members cannot take traffic, so the entry goes to the "proxy"
// group meanwhile and the report says so.
func (c *composer) target(entryID, target string, report *controlpb.CompositionReport) string {
	if slices.Contains(outbounds, target) {
		return target
	}
	g, ok := c.byID[target]
	reason := ""
	switch {
	case !ok && strings.HasPrefix(target, regionPrefix):
		reason = ReasonRegionEmpty
	case !ok, g.empty():
		reason = ReasonGroupEmpty
	}
	if reason == "" {
		return g.name
	}
	report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
		EntryId: entryID,
		Reason:  reason,
		Target:  target,
	})
	return c.byID[GroupProxy].name
}

// rules expands the enabled entries in order, one rule per item, and ends with the fallback.
func (c *composer) rules(result *Result) []any {
	var rules []any
	add := func(entryID, kind, payload, target, line string) {
		rules = append(rules, line)
		result.Lines = append(result.Lines, line)
		result.Report.Rules = append(result.Report.Rules, &controlpb.Rule{
			Type:    kind,
			Payload: payload,
			Proxy:   target,
			EntryId: entryID,
		})
	}
	for i := range c.plan.Rules {
		entry := &c.plan.Rules[i]
		if !entry.enabled() {
			continue
		}
		target := c.target(entry.ID, entry.Target, result.Report)
		for _, item := range entry.Items {
			add(entry.ID, item.Type, item.Value, target, item.line(target))
		}
	}
	fallback := c.target(FallbackEntry, c.plan.Fallback, result.Report)
	add(FallbackEntry, "MATCH", "", fallback, "MATCH,"+fallback)
	return rules
}
