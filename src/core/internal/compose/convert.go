package compose

import (
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"net/netip"
	"regexp"
	"slices"
	"strings"

	"github.com/dlclark/regexp2"
	RC "github.com/metacubex/mihomo/rules/common"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Reasons a converted rule is left out (SkippedRule.reason).
const (
	SkipRuleSet    = "rule_set"
	SkipSubRule    = "sub_rule"
	SkipTarget     = "target"
	SkipAfterMatch = "after_match"
	SkipInvalid    = "invalid"
)

// builtinAliases are the group names that merge into the built-in groups, in either language.
var builtinAliases = map[string]string{
	"节点选择": GroupProxy, "Proxy": GroupProxy,
	"自动选择": GroupAuto, "Auto": GroupAuto,
}

// Categories are the GEO data's category names, lower-cased; a nil list is not checked.
type Categories struct {
	Geosite, Geoip []string
}

// Conversion is what Convert reads besides the YAML.
type Conversion struct {
	// SourceID is the pool's subscription the YAML came from; empty for a configuration
	// imported on its own.
	SourceID   string
	Locale     string
	InfoFilter string
	// Categories leave out GEOSITE and GEOIP rules whose category the data does not have,
	// which mihomo would refuse.
	Categories Categories
}

// The UI's rule entry kinds (docs/routing.md §8.5): the core only reads the items, but the
// conversion picks the kind the routing page edits them with.
const (
	entryDomain   = "domain"
	entryIP       = "ip"
	entryCategory = "category"
	entryRaw      = "raw"
)

// uiDomain is a domain as the routing page's domain list accepts it (models/plans.py).
var uiDomain = regexp.MustCompile(`^[\p{L}\p{N}\p{M}_-]+(\.[\p{L}\p{N}\p{M}_-]+)*$`)

// converter turns one configuration into a plan.
type converter struct {
	options Conversion
	plan    *Plan
	out     *controlpb.ConvertToPlanResponse
	// ids maps the configuration's group names to the plan's group ids.
	ids map[string]string
	// nodes are the configuration's node names in order; eligible are the ones the pool
	// takes, with their regions.
	nodes    []string
	eligible map[string]string
}

// Convert makes a plan from a subscription's or a mihomo configuration's groups and rules
// (ConvertToPlan, docs/routing.md §4.7):
//
//   - groups become custom groups, except those named as a built-in group, which merge into
//     it: whatever named them names the built-in group, which keeps its own members;
//   - GEOSITE and GEOIP become category entries; DOMAIN, DOMAIN-SUFFIX and DOMAIN-KEYWORD
//     domain entries; IP-CIDR and IP-CIDR6 IP-range entries; neighbouring rules of one kind
//     with the same target share an entry, which matches as they did;
//   - other rules stay as raw entries, one each; MATCH becomes the fallback;
//   - RULE-SET and SUB-RULE need the configuration's rule-providers and sub-rules, which a
//     plan does not hold, so they are skipped, as are rules after MATCH and rules with a
//     target that is neither a group nor an outbound.
func Convert(data []byte, options Conversion) (*controlpb.ConvertToPlanResponse, error) {
	switch options.Locale {
	case "":
		options.Locale = localeZH
	case localeZH, localeEN:
	default:
		return nil, fmt.Errorf("unknown locale %q", options.Locale)
	}
	filter, err := newInfoFilter(options.InfoFilter)
	if err != nil {
		return nil, err
	}
	parsed, err := parseSubscription(data)
	if err != nil {
		return nil, err
	}
	groups, err := mappings(parsed.profile["proxy-groups"], "proxy-groups")
	if err != nil {
		return nil, err
	}
	rules, err := ruleLines(parsed.profile["rules"])
	if err != nil {
		return nil, err
	}
	if len(groups) == 0 && len(rules) == 0 {
		return nil, errors.New("it has neither groups nor rules")
	}
	c := &converter{
		options:  options,
		plan:     &Plan{Version: PlanVersion, Locale: options.Locale, Fallback: GroupProxy},
		out:      &controlpb.ConvertToPlanResponse{},
		ids:      map[string]string{},
		eligible: map[string]string{},
	}
	for name := range parsed.named {
		c.nodes = append(c.nodes, name)
		if !filter.matches(name) {
			c.eligible[name] = RegionOf(name)
		}
	}
	c.nameGroups(groups)
	for _, raw := range groups {
		if planned := c.group(raw); planned != nil {
			c.plan.Groups = append(c.plan.Groups, *planned)
		}
	}
	c.rules(rules)
	plan, err := json.Marshal(c.plan)
	if err != nil {
		return nil, err
	}
	// The plan must read back as the core reads plans; anything else is a fault here.
	if _, err := ParsePlan(plan); err != nil {
		return nil, fmt.Errorf("the converted plan: %w", err)
	}
	c.out.PlanJson = string(plan)
	c.out.EntryCount = uint32(len(c.plan.Rules))
	return c.out, nil
}

func mappings(raw any, key string) ([]map[string]any, error) {
	if raw == nil {
		return nil, nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil, fmt.Errorf("%s must be a list", key)
	}
	var out []map[string]any
	for _, item := range items {
		if mapping, ok := item.(map[string]any); ok {
			out = append(out, mapping)
		}
	}
	return out, nil
}

func ruleLines(raw any) ([]string, error) {
	if raw == nil {
		return nil, nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil, errors.New("rules must be a list")
	}
	var out []string
	for _, item := range items {
		if line, ok := item.(string); ok && strings.TrimSpace(line) != "" {
			out = append(out, line)
		}
	}
	return out, nil
}

// planID is an id for the plan's n-th group or rule, in the style the UI makes its own.
func planID(prefix string, n int, label string) string {
	hash := fnv.New64a()
	fmt.Fprintf(hash, "%d\x00%s", n, label)
	return fmt.Sprintf("%s-%012x", prefix, hash.Sum64()&0xffffffffffff)
}

// nameGroups gives every group an id, and the custom ones a name the plan accepts: one no
// built-in, region or other group takes, without commas or line breaks.
func (c *converter) nameGroups(groups []map[string]any) {
	taken := map[string]bool{providerPrefix: true}
	for _, name := range reservedNames {
		taken[name] = true
	}
	for _, id := range []string{GroupProxy, GroupAuto} {
		taken[builtinName(id, c.options.Locale)] = true
	}
	for _, region := range Regions {
		taken[region.groupName(c.options.Locale)] = true
	}
	for n, raw := range groups {
		name, _ := raw["name"].(string)
		if name == "" || slices.Contains(reservedNames, name) || c.ids[name] != "" {
			continue
		}
		c.out.GroupCount++
		if id, ok := builtinAliases[name]; ok {
			c.ids[name] = id
			c.out.MergedGroupCount++
			continue
		}
		c.ids[name] = planID("g", n, name)
	}
	for n, raw := range groups {
		name, _ := raw["name"].(string)
		id := c.ids[name]
		if id == "" || id == GroupProxy || id == GroupAuto {
			continue
		}
		clean := strings.Join(strings.FieldsFunc(name, func(r rune) bool {
			return r == ',' || r == '\r' || r == '\n'
		}), " ")
		clean = strings.TrimSpace(clean)
		if clean == "" || strings.HasPrefix(clean, providerPrefix) {
			clean = fmt.Sprintf("Group %d", n+1)
		}
		unique := clean
		for suffix := 2; taken[unique]; suffix++ {
			unique = fmt.Sprintf("%s %d", clean, suffix)
		}
		taken[unique] = true
		raw[convertedName] = unique
	}
}

// convertedName keeps a custom group's plan name in its mapping while converting.
const convertedName = "\x00plan-name"

func (c *converter) group(raw map[string]any) *PlanGroup {
	name, _ := raw["name"].(string)
	id := c.ids[name]
	planName, _ := raw[convertedName].(string)
	if id == "" || planName == "" {
		return nil
	}
	planned := &PlanGroup{ID: id, Kind: kindCustom, Name: planName, Type: "select", Members: &Members{}}
	if kind, _ := raw["type"].(string); slices.Contains(groupTypes, kind) {
		planned.Type = kind
	}
	planned.Hidden, _ = raw["hidden"].(bool)
	c.options.groupOptions(raw, planned)
	members := planned.Members
	var picked []string
	for _, item := range stringList(raw["proxies"]) {
		switch {
		case slices.Contains(outbounds, item):
			members.Others = appendNew(members.Others, item)
		case c.ids[item] != "":
			if other := c.ids[item]; other != id {
				members.Others = appendNew(members.Others, other)
			}
		case slices.Contains(c.nodes, item):
			picked = appendNew(picked, item)
		}
	}
	filters, excludes := splitFilter(raw["filter"]), splitFilter(raw["exclude-filter"])
	includeAll := len(stringList(raw["use"])) != 0
	for _, key := range []string{"include-all", "include-all-proxies", "include-all-providers"} {
		if on, _ := raw[key].(bool); on {
			includeAll = true
		}
	}
	if includeAll {
		var patterns []string
		for _, filter := range filters {
			if region := regionWithPattern(filter); region != "" {
				members.Regions = appendNew(members.Regions, region)
			} else {
				patterns = append(patterns, filter)
			}
		}
		switch {
		case len(filters) == 0:
			members.All = true
		case len(patterns) != 0:
			members.Name = &NameMatch{Mode: "regex", Pattern: alternation(patterns)}
		}
	}
	if len(excludes) != 0 {
		members.Exclude = &NameMatch{Mode: "regex", Pattern: alternation(excludes)}
	}
	if !members.All {
		c.pick(members, picked)
	}
	if !validPattern(members.Name) || !validPattern(members.Exclude) {
		// A filter mihomo's own syntax reads but the plan cannot use: the group keeps its
		// other members.
		members.Name, members.Exclude = nil, nil
	}
	return planned
}

// pick adds the nodes a group lists by name: all of the pool's nodes as "all", whole regions
// as regions, and the rest as picked nodes of the source, or by exact names without one.
func (c *converter) pick(members *Members, picked []string) {
	var listed []string
	for _, name := range picked {
		if _, ok := c.eligible[name]; ok {
			listed = append(listed, name)
		}
	}
	if len(listed) == 0 {
		return
	}
	if len(listed) == len(c.eligible) && len(members.Regions) == 0 && members.Name == nil {
		members.All = true
		return
	}
	var regions []string
	whole := true
	for _, name := range listed {
		regions = appendNew(regions, c.eligible[name])
	}
	for _, region := range regions {
		for name, of := range c.eligible {
			if of == region && !slices.Contains(listed, name) {
				whole = false
			}
		}
	}
	if whole {
		for _, region := range Regions {
			if slices.Contains(regions, region.Code) {
				members.Regions = appendNew(members.Regions, region.Code)
			}
		}
		return
	}
	if c.options.SourceID != "" {
		for _, name := range listed {
			members.Nodes = append(members.Nodes, NodeRef{Source: c.options.SourceID, Name: name})
		}
		return
	}
	names := make([]string, len(listed))
	for i, name := range listed {
		names[i] = regexp2.Escape(name)
	}
	exact := "^(?:" + strings.Join(names, "|") + ")$"
	if members.Name != nil {
		exact = members.Name.Pattern + "|" + exact
	}
	members.Name = &NameMatch{Mode: "regex", Pattern: exact}
}

func (o Conversion) groupOptions(raw map[string]any, planned *PlanGroup) {
	options := &planned.Options
	options.URL, _ = raw["url"].(string)
	options.Interval = nonNegativeInt(raw["interval"])
	if planned.Type == "url-test" {
		options.Tolerance = nonNegativeInt(raw["tolerance"])
	}
	options.Timeout = nonNegativeInt(raw["timeout"])
	if lazy, ok := raw["lazy"].(bool); ok {
		options.Lazy = &lazy
	}
	if strategy, _ := raw["strategy"].(string); planned.Type == "load-balance" &&
		slices.Contains(loadBalanceStrategies, strategy) {
		options.Strategy = strategy
	}
}

func nonNegativeInt(raw any) int {
	switch v := raw.(type) {
	case int:
		return max(v, 0)
	case int64:
		return int(max(v, 0))
	case uint64:
		return int(min(v, 1<<31))
	case float64:
		return int(max(v, 0))
	}
	return 0
}

func stringList(raw any) []string {
	items, _ := raw.([]any)
	var out []string
	for _, item := range items {
		if text, ok := item.(string); ok && text != "" {
			out = append(out, text)
		}
	}
	return out
}

func appendNew(list []string, item string) []string {
	if slices.Contains(list, item) {
		return list
	}
	return append(list, item)
}

// splitFilter reads mihomo's filter list: patterns separated by backticks.
func splitFilter(raw any) []string {
	text, _ := raw.(string)
	var out []string
	for part := range strings.SplitSeq(text, "`") {
		if part != "" {
			out = append(out, strings.ReplaceAll(part, `\x60`, "`"))
		}
	}
	return out
}

func alternation(patterns []string) string {
	if len(patterns) == 1 {
		return patterns[0]
	}
	parts := make([]string, len(patterns))
	for i, pattern := range patterns {
		parts[i] = "(?:" + pattern + ")"
	}
	return strings.Join(parts, "|")
}

// regionWithPattern is the region whose table pattern a filter is, as Export writes them.
func regionWithPattern(filter string) string {
	for _, region := range Regions {
		if region.Pattern != "" && region.Pattern == filter {
			return region.Code
		}
	}
	return ""
}

func validPattern(match *NameMatch) bool {
	return match == nil || match.compile() == nil
}

// rules converts the rule lines in order.
func (c *converter) rules(lines []string) {
	matched := false
	for _, line := range lines {
		c.out.RuleCount++
		if matched {
			c.skip(line, SkipAfterMatch)
			continue
		}
		kind, payload, target, params := RC.ParseRulePayload(line, true)
		if kind == "MATCH" {
			if id, ok := c.target(target); ok {
				c.plan.Fallback = id
				matched = true
			} else {
				c.skip(line, SkipTarget)
			}
			continue
		}
		upper := strings.ToUpper(payload)
		switch {
		case kind == "RULE-SET", slices.Contains([]string{"AND", "OR", "NOT"}, kind) && strings.Contains(upper, "RULE-SET"):
			c.skip(line, SkipRuleSet)
			continue
		case kind == "SUB-RULE", slices.Contains([]string{"AND", "OR", "NOT"}, kind) && strings.Contains(upper, "SUB-RULE"):
			c.skip(line, SkipSubRule)
			continue
		}
		if payload == "" {
			c.skip(line, SkipInvalid)
			continue
		}
		id, ok := c.target(target)
		if !ok {
			c.skip(line, SkipTarget)
			continue
		}
		item := RuleItem{Type: kind, Value: payload}
		for _, param := range params {
			switch {
			case !strings.EqualFold(param, "no-resolve"):
				item.Type = "" // a parameter the plan has no field for
			case slices.Contains(resolvingTypes, kind):
				// Other types ignore it, as mihomo does.
				item.NoResolve = true
			}
		}
		if item.check() != nil || !c.known(item) {
			c.skip(line, SkipInvalid)
			continue
		}
		c.add(item, id)
	}
}

func (c *converter) skip(line, reason string) {
	c.out.Skipped = append(c.out.Skipped, &controlpb.SkippedRule{Rule: line, Reason: reason})
}

func (c *converter) target(name string) (string, bool) {
	if slices.Contains(outbounds, name) {
		return name, true
	}
	id, ok := c.ids[name]
	return id, ok && id != ""
}

// known leaves out the rules mihomo would refuse that are cheap to spot here: categories
// the GEO data does not have, and regular expressions and ranges that do not parse.
func (c *converter) known(item RuleItem) bool {
	switch item.Type {
	case "GEOSITE":
		return c.options.Categories.Geosite == nil ||
			slices.Contains(c.options.Categories.Geosite, strings.ToLower(item.Value))
	case "GEOIP":
		return c.options.Categories.Geoip == nil ||
			slices.Contains(c.options.Categories.Geoip, strings.ToLower(item.Value))
	case "DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX":
		_, err := regexp2.Compile(item.Value, regexp2.None)
		return err == nil
	case "IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR":
		_, err := netip.ParsePrefix(item.Value)
		return err == nil
	}
	return true
}

// add puts a rule in the plan: with the entry before it when both are domains, or both IP
// ranges alike in no-resolve, with one target; in an entry of its own otherwise.
func (c *converter) add(item RuleItem, target string) {
	kind := entryRaw
	switch item.Type {
	case "GEOSITE", "GEOIP":
		kind = entryCategory
		item.Value = strings.ToLower(item.Value)
	case "DOMAIN", "DOMAIN-SUFFIX":
		if uiDomain.MatchString(item.Value) {
			kind = entryDomain
		}
	case "DOMAIN-KEYWORD":
		if !strings.ContainsAny(item.Value, " \t") {
			kind = entryDomain
		}
	case "IP-CIDR", "IP-CIDR6":
		if prefix, err := netip.ParsePrefix(item.Value); err == nil {
			kind = entryIP
			item.Value = prefix.Masked().String()
		}
	}
	if kind == entryRaw {
		c.out.RawRuleCount++
	}
	if n := len(c.plan.Rules); n > 0 && (kind == entryDomain || kind == entryIP) {
		last := &c.plan.Rules[n-1]
		if last.Kind == kind && last.Target == target &&
			(kind == entryDomain || last.Items[0].NoResolve == item.NoResolve) {
			last.Items = append(last.Items, item)
			return
		}
	}
	n := len(c.plan.Rules)
	c.plan.Rules = append(c.plan.Rules, RuleEntry{
		ID:     planID("r", n, item.Type+","+item.Value),
		Kind:   kind,
		Items:  []RuleItem{item},
		Target: target,
	})
}
