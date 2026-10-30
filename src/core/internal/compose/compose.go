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
	// Warp is the WARP exit's device; without it the exit is unavailable (docs/exits.md §3.3).
	Warp *controlpb.WarpAccount
	// VpnGate are the VPN Gate exit's servers, as the UI picked them; without any the exit
	// is unavailable (docs/exits.md §5.3).
	VpnGate []*controlpb.VpnGateServer
	// Mesh are the networks the user added in the app (docs/mesh.md §10.3).
	Mesh []*controlpb.MeshNetwork
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
	// The target exit has nothing to run on, or the target mesh outbound is gone: the
	// entry rejects its connections rather than go out some other way (decision D21).
	ReasonEgressUnavailable = "egress_unavailable"
	ReasonMeshMissing       = "mesh_missing"
	// An exit's via group has no members, so the exit goes through "proxy" meanwhile.
	ReasonViaEmpty = "via_empty"
	// An option of a mesh outbound that serves other devices was turned off (docs/mesh.md §4).
	ReasonMeshRestricted = "mesh_restricted"

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
	// otherIDs are the ids of the other members, in the same order.
	otherIDs []string
}

func (g *group) empty() bool { return len(g.nodes) == 0 && len(g.others) == 0 }

type composer struct {
	plan    *Plan
	sources []Source
	pool    []*node
	groups  []*group
	byID    map[string]*group
	// mesh are the mesh outbounds: the networks of the composition, then the pool's. They
	// are by their plan id too.
	mesh     []*node
	meshByID map[string]*node
	warp     *controlpb.WarpAccount
	// warpUsed is set once a rule goes to the WARP exit and it has a device.
	warpUsed bool
	vpnGate  []*controlpb.VpnGateServer
	// vpnGateUsed is set once a rule goes to the VPN Gate exit and it has servers.
	vpnGateUsed bool
	// share names the exits and mesh outbounds whether or not they run here: an exported
	// plan goes to configurations that bring their own.
	share bool
}

// newComposer reads the plan and the sources, and works out the pool and every group's
// members.
func newComposer(composition Composition) (*composer, error) {
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
	if err := checkVpnGate(composition.VpnGate); err != nil {
		return nil, fmt.Errorf("vpngate: %w", err)
	}
	c := &composer{
		plan:     plan,
		sources:  composition.Sources,
		byID:     map[string]*group{},
		meshByID: map[string]*node{},
		warp:     composition.Warp,
		vpnGate:  composition.VpnGate,
	}
	taken, err := c.groupNames()
	if err != nil {
		return nil, fmt.Errorf("plan: %w", err)
	}
	// The networks are named before the nodes: the user chose their names.
	if c.mesh, err = networkNodes(composition.Mesh, taken); err != nil {
		return nil, err
	}
	pool, mesh, err := buildPool(composition.Sources, filter, taken)
	if err != nil {
		return nil, err
	}
	c.pool, c.mesh = pool, append(c.mesh, mesh...)
	for _, n := range c.mesh {
		c.meshByID[n.id] = n
	}
	c.layOutGroups()
	for _, g := range c.groups {
		if g.kind != controlpb.GroupKind_GROUP_KIND_REGION {
			c.resolveMembers(g)
		}
	}
	return c, nil
}

// Compose assembles the configuration: the base section, one inline provider per
// source, the groups and the rules.
func Compose(composition Composition) (*Result, error) {
	c, err := newComposer(composition)
	if err != nil {
		return nil, err
	}
	var config map[string]any
	if err := yaml.Unmarshal(baseYAML, &config); err != nil {
		return nil, fmt.Errorf("base section: %w", err)
	}
	result := &Result{Config: config, Report: &controlpb.CompositionReport{}}
	config["proxy-providers"] = c.providers()
	config["proxy-groups"] = c.groupConfigs(result.Report)
	config["rules"] = c.rules(result)
	// The mesh outbounds and the exits are outbounds of their own: no provider holds them,
	// so no group's use and filter takes them in.
	var proxies []any
	for _, n := range c.mesh {
		outbound := &controlpb.MeshOutbound{Id: n.id, Name: n.name, Type: n.kind}
		if n.source >= 0 {
			outbound.SourceId = c.sources[n.source].ID
		} else if via := c.networkVia(n, result.Report); via != "" {
			n.mapping["dialer-proxy"] = via
		}
		proxies = append(proxies, n.mapping)
		result.Report.Mesh = append(result.Report.Mesh, outbound)
	}
	if c.warpUsed {
		outbound, err := c.warpOutbound(result.Report)
		if err != nil {
			return nil, fmt.Errorf("warp account: %w", err)
		}
		proxies = append(proxies, outbound)
	}
	if c.vpnGateUsed {
		outbounds, group := c.vpnGateExit(result.Report)
		proxies = append(proxies, outbounds...)
		config["proxy-groups"] = append(config["proxy-groups"].([]any), group)
	}
	if len(proxies) != 0 {
		config["proxies"] = proxies
	}
	for _, n := range c.pool {
		result.Report.Nodes = append(result.Report.Nodes, c.poolNode(n))
	}
	return result, nil
}

func (c *composer) poolNode(n *node) *controlpb.PoolNode {
	return &controlpb.PoolNode{
		Name:         n.name,
		OriginalName: n.original,
		SourceId:     c.sources[n.source].ID,
		Type:         n.kind,
		Region:       n.region,
	}
}

// Preview works out one group's members for the group editor (PreviewGroup). A region
// without nodes has no group, and so no members.
func Preview(composition Composition, groupID string) (*controlpb.PreviewGroupResponse, error) {
	c, err := newComposer(composition)
	if err != nil {
		return nil, err
	}
	g, ok := c.byID[groupID]
	if !ok {
		if code, isRegion := strings.CutPrefix(groupID, regionPrefix); isRegion {
			if _, err := regionByCode(code); err == nil {
				return &controlpb.PreviewGroupResponse{}, nil
			}
		}
		return nil, fmt.Errorf("plan: no group %q", groupID)
	}
	preview := &controlpb.PreviewGroupResponse{Others: slices.Clone(g.otherIDs)}
	for _, n := range g.nodes {
		preview.Nodes = append(preview.Nodes, c.poolNode(n))
	}
	return preview, nil
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
	// The exits' names are the plan's whether they run or not, so turning one on renames
	// nothing. The VPN Gate servers' names come with the composition.
	taken[warpName(c.plan.Locale)] = true
	taken[vpnGateName(c.plan.Locale)] = true
	for _, server := range c.vpnGate {
		taken[vpnGateServerName(server)] = true
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
	add := func(id, name string) {
		if !slices.Contains(g.others, name) {
			g.others = append(g.others, name)
			g.otherIDs = append(g.otherIDs, id)
		}
	}
	for _, other := range members.Others {
		switch {
		case other == allRegions:
			for _, candidate := range c.groups {
				if candidate.kind == controlpb.GroupKind_GROUP_KIND_REGION {
					add(candidate.id, candidate.name)
				}
			}
		case slices.Contains(outbounds, other):
			add(other, other)
		default:
			// A region without nodes has no group to join.
			if member, ok := c.byID[other]; ok {
				add(member.id, member.name)
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
// group meanwhile and the report says so. An exit or a mesh outbound that is not there
// rejects instead: going out through a node is what the user picked them to avoid, and
// a node cannot reach the user's own networks anyway.
func (c *composer) target(entryID, target string, report *controlpb.CompositionReport) string {
	if slices.Contains(outbounds, target) {
		return target
	}
	reject := func(reason string) string {
		report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
			EntryId: entryID,
			Reason:  reason,
			Target:  target,
		})
		return "REJECT"
	}
	if target == EgressWarp {
		switch {
		case c.share:
		case c.warp == nil:
			return reject(ReasonEgressUnavailable)
		default:
			c.warpUsed = true
		}
		return warpName(c.plan.Locale)
	}
	if target == EgressVpnGate {
		switch {
		case c.share:
		case len(c.vpnGate) == 0:
			return reject(ReasonEgressUnavailable)
		default:
			c.vpnGateUsed = true
		}
		return vpnGateName(c.plan.Locale)
	}
	if strings.HasPrefix(target, meshPrefix) {
		n, ok := c.meshByID[target]
		if !ok {
			return reject(ReasonMeshMissing)
		}
		return n.name
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
