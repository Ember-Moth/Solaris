package compose

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/dlclark/regexp2"
)

// PlanVersion is the plan format this core reads (docs/routing.md §8.5).
const PlanVersion = 1

const (
	localeZH = "zh"
	localeEN = "en"

	kindBuiltin = "builtin"
	kindRegion  = "region"
	kindCustom  = "custom"

	// The built-in groups' ids. Region groups are "region:" and the region code.
	GroupProxy   = "proxy"
	GroupAuto    = "auto"
	regionPrefix = "region:"
	// allRegions stands for every region group in members.others.
	allRegions = "region:*"

	// FallbackEntry is the entry id of the fallback rule, MATCH.
	FallbackEntry = "fallback"

	// EgressWarp is the WARP exit as a rule target (docs/exits.md §3.3); its settings are in
	// the plan's egress.warp.
	EgressWarp = "egress:warp"
	// meshPrefix names a mesh outbound as a rule target: "mesh:<subscription id>/<name in
	// the subscription>" (docs/mesh.md §3), or "mesh:<network id>" for a network the user
	// added in the app (§10.3).
	meshPrefix = "mesh:"
)

var groupTypes = []string{"select", "url-test", "fallback", "load-balance"}

var loadBalanceStrategies = []string{"consistent-hashing", "round-robin", "sticky-sessions"}

// outbounds are mihomo's built-in outbounds that a rule or a group may name.
var outbounds = []string{"DIRECT", "REJECT", "REJECT-DROP"}

// reservedNames are taken in mihomo's proxy table, so no group or node may use them.
var reservedNames = []string{"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}

// Plan is the JSON the UI sends: groups, rule entries and the fallback. Fields the core
// does not know are ignored, so the UI may keep its own (such as a service id) in them.
type Plan struct {
	Version int    `json:"version"`
	Locale  string `json:"locale"`
	// TestURL is the delay test URL of every group that does not set its own.
	TestURL  string      `json:"test_url,omitempty"`
	Groups   []PlanGroup `json:"groups,omitempty"`
	Rules    []RuleEntry `json:"rules,omitempty"`
	Fallback string      `json:"fallback"`
	Egress   Egress      `json:"egress,omitzero"`
}

// Egress holds what the plan says of the chained exits (docs/exits.md §7.1); the
// exits' accounts and endpoints come with the composition instead.
type Egress struct {
	Warp ExitSettings `json:"warp,omitzero"`
}

type ExitSettings struct {
	// Via is the group whose node the exit connects through: a group id, "proxy" when empty.
	Via string `json:"via,omitempty"`
}

type PlanGroup struct {
	ID      string       `json:"id"`
	Kind    string       `json:"kind"`
	Name    string       `json:"name,omitempty"`
	Type    string       `json:"type,omitempty"`
	Hidden  bool         `json:"hidden,omitempty"`
	Members *Members     `json:"members,omitempty"`
	Options GroupOptions `json:"options,omitzero"`
}

// Members chooses a built-in or custom group's members (docs/routing.md §4.5): the nodes
// in any of the regions, from any of the sources or whose names match, less those that
// Exclude matches; then the picked nodes and the other members, which Exclude never removes.
// All starts from every node in the pool instead.
type Members struct {
	All     bool       `json:"all,omitempty"`
	Regions []string   `json:"regions,omitempty"`
	Sources []string   `json:"sources,omitempty"`
	Name    *NameMatch `json:"name,omitempty"`
	Exclude *NameMatch `json:"exclude,omitempty"`
	Nodes   []NodeRef  `json:"nodes,omitempty"`
	// Others are group ids, "region:*" for every region group, DIRECT, REJECT or REJECT-DROP.
	Others []string `json:"others,omitempty"`
}

// NameMatch tests node names as the subscription wrote them: a keyword, ignoring case,
// or a regular expression in mihomo's syntax (regexp2).
type NameMatch struct {
	Mode    string `json:"mode"` // "keyword" or "regex"
	Pattern string `json:"pattern"`

	re *regexp2.Regexp
}

// NodeRef picks a node by its source and its name in that source, which a rename for a
// clashing name does not change.
type NodeRef struct {
	Source string `json:"source"`
	Name   string `json:"name"`
}

type GroupOptions struct {
	URL       string `json:"url,omitempty"`
	Interval  int    `json:"interval,omitempty"`  // seconds between delay tests
	Tolerance int    `json:"tolerance,omitempty"` // url-test: milliseconds a faster node must win by
	Timeout   int    `json:"timeout,omitempty"`   // milliseconds per delay test
	Lazy      *bool  `json:"lazy,omitempty"`      // test only while the group is in use; mihomo's default is true
	Strategy  string `json:"strategy,omitempty"`  // load-balance only
}

// RuleEntry is one line of the rule list: an intent the core expands into one rule per item.
type RuleEntry struct {
	ID string `json:"id"`
	// Kind is the UI's: "service", "category", "domain", "ip" or "raw". The core only reads
	// the items.
	Kind    string     `json:"kind,omitempty"`
	Enabled *bool      `json:"enabled,omitempty"` // absent means enabled
	Items   []RuleItem `json:"items"`
	Target  string     `json:"target"`
}

func (e *RuleEntry) enabled() bool { return e.Enabled == nil || *e.Enabled }

// RuleItem is one mihomo rule without its target: GEOSITE,telegram or IP-CIDR,1.0.0.0/8.
type RuleItem struct {
	Type      string `json:"type"`
	Value     string `json:"value"`
	NoResolve bool   `json:"no_resolve,omitempty"`
}

// itemTypes are the rule types an item may have: every one that needs nothing outside the
// rules, so not RULE-SET or SUB-RULE, and not MATCH, which only the fallback writes.
var itemTypes = []string{
	"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "DOMAIN-WILDCARD", "GEOSITE",
	"GEOIP", "IP-CIDR", "IP-CIDR6", "IP-SUFFIX", "IP-ASN",
	"SRC-GEOIP", "SRC-IP-ASN", "SRC-IP-CIDR", "SRC-IP-SUFFIX", "SRC-PORT", "DST-PORT",
	"IN-PORT", "IN-TYPE", "IN-USER", "IN-NAME",
	"PROCESS-NAME", "PROCESS-PATH", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX",
	"PROCESS-NAME-WILDCARD", "PROCESS-PATH-WILDCARD", "NETWORK", "UID", "DSCP",
	"AND", "OR", "NOT",
}

// commaTypes may hold commas in their value; mihomo then reads the target from the end
// and takes no parameters (rules/common.ParseRulePayload).
var commaTypes = []string{"AND", "OR", "NOT", "DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX"}

// resolvingTypes match the destination IP and take no-resolve.
var resolvingTypes = []string{"GEOIP", "IP-CIDR", "IP-CIDR6", "IP-SUFFIX", "IP-ASN"}

// ParsePlan reads and checks a plan. Errors name the group or entry at fault.
func ParsePlan(data []byte) (*Plan, error) {
	if len(bytes.TrimSpace(data)) == 0 {
		return nil, errors.New("the plan is empty")
	}
	var plan Plan
	if err := json.Unmarshal(data, &plan); err != nil {
		return nil, err
	}
	if plan.Version != PlanVersion {
		return nil, fmt.Errorf("plan version %d is not supported; this core reads version %d", plan.Version, PlanVersion)
	}
	switch plan.Locale {
	case "":
		plan.Locale = localeZH
	case localeZH, localeEN:
	default:
		return nil, fmt.Errorf("unknown locale %q", plan.Locale)
	}
	if err := plan.checkGroups(); err != nil {
		return nil, err
	}
	if err := plan.checkRules(); err != nil {
		return nil, err
	}
	if err := plan.checkEgress(); err != nil {
		return nil, err
	}
	return &plan, nil
}

func (p *Plan) group(id string) *PlanGroup {
	for i := range p.Groups {
		if p.Groups[i].ID == id {
			return &p.Groups[i]
		}
	}
	return nil
}

func (p *Plan) checkGroups() error {
	seen := map[string]bool{}
	for i := range p.Groups {
		group := &p.Groups[i]
		if seen[group.ID] {
			return fmt.Errorf("group %q appears twice", group.ID)
		}
		seen[group.ID] = true
		label := fmt.Sprintf("group %q", group.ID)
		switch group.Kind {
		case kindBuiltin:
			if group.ID != GroupProxy && group.ID != GroupAuto {
				return fmt.Errorf("%s: the built-in groups are %q and %q", label, GroupProxy, GroupAuto)
			}
		case kindRegion:
			code, ok := strings.CutPrefix(group.ID, regionPrefix)
			if !ok {
				return fmt.Errorf("%s: a region group's id is %q and the region code", label, regionPrefix)
			}
			if _, err := regionByCode(code); err != nil {
				return fmt.Errorf("%s: %w", label, err)
			}
			group.Members = nil
		case kindCustom:
			if group.ID == "" || group.ID == GroupProxy || group.ID == GroupAuto ||
				strings.HasPrefix(group.ID, regionPrefix) || slices.Contains(reservedNames, group.ID) {
				return fmt.Errorf("%s: a custom group needs an id of its own", label)
			}
			if group.Name == "" {
				return fmt.Errorf("%s: a custom group needs a name", label)
			}
		default:
			return fmt.Errorf("%s: unknown kind %q", label, group.Kind)
		}
		if group.Type != "" && !slices.Contains(groupTypes, group.Type) {
			return fmt.Errorf("%s: unknown type %q", label, group.Type)
		}
		if group.Name != "" {
			if err := checkName(group.Name); err != nil {
				return fmt.Errorf("%s: %w", label, err)
			}
		}
		if err := group.Options.check(); err != nil {
			return fmt.Errorf("%s: %w", label, err)
		}
		if group.Members != nil {
			if err := p.checkMembers(group); err != nil {
				return fmt.Errorf("%s: %w", label, err)
			}
		}
	}
	return nil
}

// checkName keeps a name usable in a rule: mihomo splits rules at commas and trims spaces.
func checkName(name string) error {
	switch {
	case strings.TrimSpace(name) != name || name == "":
		return fmt.Errorf("name %q is empty or starts or ends with a space", name)
	case strings.ContainsAny(name, ",\r\n"):
		return fmt.Errorf("name %q holds a comma or a line break", name)
	case slices.Contains(reservedNames, name):
		return fmt.Errorf("name %q is mihomo's own", name)
	}
	return nil
}

func (o *GroupOptions) check() error {
	if o.Interval < 0 || o.Tolerance < 0 || o.Timeout < 0 {
		return errors.New("interval, tolerance and timeout cannot be negative")
	}
	if o.Strategy != "" && !slices.Contains(loadBalanceStrategies, o.Strategy) {
		return fmt.Errorf("unknown load-balance strategy %q", o.Strategy)
	}
	return nil
}

func (p *Plan) checkMembers(group *PlanGroup) error {
	members := group.Members
	for _, code := range members.Regions {
		if _, err := regionByCode(code); err != nil {
			return err
		}
	}
	for _, match := range []*NameMatch{members.Name, members.Exclude} {
		if match == nil {
			continue
		}
		if err := match.compile(); err != nil {
			return err
		}
	}
	for _, other := range members.Others {
		if other == group.ID {
			return errors.New("a group cannot be its own member")
		}
		if err := p.checkReference(other); err != nil {
			return err
		}
	}
	return nil
}

func (m *NameMatch) compile() error {
	pattern := m.Pattern
	switch m.Mode {
	case "keyword":
		pattern = "(?i)" + regexp2.Escape(m.Pattern)
	case "regex":
	default:
		return fmt.Errorf("unknown name match mode %q", m.Mode)
	}
	if m.Pattern == "" {
		return errors.New("a name match needs a pattern")
	}
	re, err := regexp2.Compile(pattern, regexp2.None)
	if err != nil {
		return fmt.Errorf("name pattern %q: %w", m.Pattern, err)
	}
	m.re = re
	return nil
}

func (m *NameMatch) matches(name string) bool {
	found, _ := m.re.MatchString(name)
	return found
}

// checkReference accepts what a rule or a group may point at: a group the plan or the core
// defines, a region, every region, or a built-in outbound.
func (p *Plan) checkReference(target string) error {
	switch {
	case slices.Contains(outbounds, target), target == GroupProxy, target == GroupAuto, target == allRegions:
		return nil
	case strings.HasPrefix(target, regionPrefix):
		_, err := regionByCode(strings.TrimPrefix(target, regionPrefix))
		return err
	case p.group(target) != nil && p.group(target).Kind == kindCustom:
		return nil
	}
	return fmt.Errorf("unknown target %q", target)
}

func (p *Plan) checkRules() error {
	seen := map[string]bool{}
	for i := range p.Rules {
		entry := &p.Rules[i]
		if entry.ID == "" || entry.ID == FallbackEntry {
			return fmt.Errorf("rule %d needs an id other than %q", i+1, FallbackEntry)
		}
		if seen[entry.ID] {
			return fmt.Errorf("rule %q appears twice", entry.ID)
		}
		seen[entry.ID] = true
		label := fmt.Sprintf("rule %q", entry.ID)
		if err := p.checkTarget(entry.Target); err != nil {
			return fmt.Errorf("%s: %w", label, err)
		}
		if len(entry.Items) == 0 {
			return fmt.Errorf("%s has no items", label)
		}
		for j := range entry.Items {
			if err := entry.Items[j].check(); err != nil {
				return fmt.Errorf("%s, item %d: %w", label, j+1, err)
			}
		}
	}
	if p.Fallback == "" {
		p.Fallback = GroupProxy
	}
	if err := p.checkTarget(p.Fallback); err != nil {
		return fmt.Errorf("fallback: %w", err)
	}
	return nil
}

// checkTarget accepts what a rule or the fallback may send traffic to: what a group may
// hold, but not every region at once, and also the WARP exit and mesh outbounds, which no
// group holds.
func (p *Plan) checkTarget(target string) error {
	switch name, isMesh := strings.CutPrefix(target, meshPrefix); {
	case target == allRegions:
		return fmt.Errorf("%q is not a target", allRegions)
	case target == EgressWarp:
		return nil
	case isMesh:
		source, original, ok := strings.Cut(name, "/")
		if !ok && IsNetworkID(name) || ok && source != "" && original != "" {
			return nil
		}
		return fmt.Errorf("mesh target %q is not %q and a network id, or a subscription id, / and a name", target, meshPrefix)
	}
	return p.checkReference(target)
}

// checkEgress accepts a group, built-in, region or custom, as an exit's via.
func (p *Plan) checkEgress() error {
	via := p.Egress.Warp.Via
	if via == "" {
		return nil
	}
	if slices.Contains(outbounds, via) || via == allRegions {
		return fmt.Errorf("egress.warp.via: %q is not a group", via)
	}
	if err := p.checkReference(via); err != nil {
		return fmt.Errorf("egress.warp.via: %w", err)
	}
	return nil
}

func (item *RuleItem) check() error {
	item.Type = strings.ToUpper(strings.TrimSpace(item.Type))
	item.Value = strings.TrimSpace(item.Value)
	if !slices.Contains(itemTypes, item.Type) {
		return fmt.Errorf("unknown rule type %q", item.Type)
	}
	switch {
	case item.Value == "":
		return errors.New("the value is empty")
	case strings.ContainsAny(item.Value, "\r\n"):
		return errors.New("the value holds a line break")
	case strings.Contains(item.Value, ",") && !slices.Contains(commaTypes, item.Type):
		return fmt.Errorf("a %s value cannot hold a comma", item.Type)
	case item.NoResolve && !slices.Contains(resolvingTypes, item.Type):
		return fmt.Errorf("no_resolve only applies to %s", strings.Join(resolvingTypes, ", "))
	}
	return nil
}

// line is the item as a mihomo rule with its target.
func (item *RuleItem) line(target string) string {
	line := item.Type + "," + item.Value + "," + target
	if item.NoResolve {
		line += ",no-resolve"
	}
	return line
}
