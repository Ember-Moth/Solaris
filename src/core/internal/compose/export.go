package compose

import (
	"slices"
	"strings"

	"github.com/dlclark/regexp2"
	"go.yaml.in/yaml/v3"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Export writes the plan as proxy-groups and rules for another mihomo configuration
// (ExportPlan, format SHARE): the groups and rules Compose generates, with each group's
// members written as conditions mihomo evaluates where the configuration runs, since the
// pool's providers and node definitions stay here (docs/routing.md §4.7).
//
//   - regions and name matches become include-all with one filter each, all nodes
//     include-all alone, and the exclusion exclude-filter;
//   - the other members, the picked nodes and the nodes of the chosen subscriptions are
//     listed in proxies, nodes by the names their subscriptions gave them;
//   - the region groups filter with the region table's patterns; "other regions" has no
//     pattern, so it lists its nodes.
//
// mihomo applies exclude-filter to the listed members too, and a node whose name two
// region patterns find lands in both region groups; within this pool neither happens.
func Export(composition Composition) ([]byte, error) {
	c, err := newComposer(composition)
	if err != nil {
		return nil, err
	}
	groups := make([]any, 0, len(c.groups))
	for _, g := range c.groups {
		groups = append(groups, c.shareGroup(g))
	}
	result := &Result{Report: &controlpb.CompositionReport{}}
	return MarshalOrdered(map[string]any{
		"proxy-groups": groups,
		"rules":        c.rules(result),
	})
}

func (c *composer) shareGroup(g *group) map[string]any {
	config := map[string]any{"name": g.name, "type": g.plan.Type}
	var filters, listed []string
	includeAll := false
	if g.kind == controlpb.GroupKind_GROUP_KIND_REGION {
		region, _ := regionByCode(g.region)
		if region.Pattern != "" {
			filters, includeAll = []string{region.Pattern}, true
		} else {
			for _, n := range g.nodes {
				listed = append(listed, n.original)
			}
		}
	} else {
		members := g.plan.Members
		listed = slices.Clone(g.others)
		for _, code := range members.Regions {
			region, _ := regionByCode(code)
			if region.Pattern == "" {
				continue
			}
			filters = append(filters, region.Pattern)
		}
		if members.Name != nil {
			filters = append(filters, members.Name.expression())
		}
		includeAll = members.All || len(filters) != 0
		if members.All {
			filters = nil
		}
		// The nodes no condition here can express: the ones from the chosen subscriptions,
		// "other regions", and the picked ones.
		for _, n := range g.nodes {
			if includeAll && (members.All || slices.Contains(members.Regions, n.region) && n.region != OtherRegion ||
				members.Name != nil && members.Name.matches(n.original)) {
				continue
			}
			if !slices.Contains(listed, n.original) {
				listed = append(listed, n.original)
			}
		}
		if members.Exclude != nil {
			config["exclude-filter"] = filterText(members.Exclude.expression())
		}
	}
	if includeAll {
		config["include-all"] = true
	}
	if len(filters) != 0 {
		parts := make([]string, len(filters))
		for i, filter := range filters {
			parts[i] = filterText(filter)
		}
		// mihomo reads a backtick as the start of another filter.
		config["filter"] = strings.Join(parts, "`")
	}
	if len(listed) != 0 {
		config["proxies"] = listed
	}
	if !includeAll && len(listed) == 0 {
		config["proxies"] = []any{compatible}
	}
	if g.plan.Hidden {
		config["hidden"] = true
	}
	c.applyOptions(config, g.plan)
	return config
}

// filterText keeps a pattern whole inside mihomo's backtick-separated filter list.
func filterText(pattern string) string {
	return strings.ReplaceAll(pattern, "`", `\x60`)
}

// expression is the name match as a regular expression in mihomo's syntax.
func (m *NameMatch) expression() string {
	if m.Mode == "keyword" {
		return "(?i)" + regexp2.Escape(m.Pattern)
	}
	return m.Pattern
}

// topKeys and itemKeys put the keys people look for first, in the configuration and in
// its groups and nodes; the rest follow in alphabetical order.
var (
	topKeys = []string{
		"mode", "log-level", "mixed-port", "port", "socks-port", "allow-lan", "bind-address",
		"ipv6", "unified-delay", "tcp-concurrent", "profile", "geodata-mode", "geodata-loader",
		"geo-auto-update", "geo-update-interval", "geox-url", "sniffer", "tun", "dns", "hosts",
		"proxies", "proxy-providers", "proxy-groups", "rule-providers", "rules",
	}
	itemKeys = []string{
		"name", "type", "server", "port", "proxies", "use", "include-all", "filter",
		"exclude-filter", "url", "interval", "tolerance", "timeout", "lazy", "strategy", "hidden",
	}
)

// MarshalOrdered writes a configuration as YAML with its keys in a readable order; lists
// keep theirs.
func MarshalOrdered(doc map[string]any) ([]byte, error) {
	node, err := orderedNode(doc, topKeys)
	if err != nil {
		return nil, err
	}
	return yaml.Marshal(node)
}

func orderedNode(value any, order []string) (*yaml.Node, error) {
	switch v := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		rank := func(key string) int {
			if i := slices.Index(order, key); i >= 0 {
				return i
			}
			return len(order)
		}
		slices.SortFunc(keys, func(a, b string) int {
			if ra, rb := rank(a), rank(b); ra != rb {
				return ra - rb
			}
			return strings.Compare(a, b)
		})
		node := &yaml.Node{Kind: yaml.MappingNode}
		for _, key := range keys {
			child, err := orderedNode(v[key], itemKeys)
			if err != nil {
				return nil, err
			}
			node.Content = append(node.Content, &yaml.Node{Kind: yaml.ScalarNode, Value: key}, child)
		}
		return node, nil
	case []any:
		node := &yaml.Node{Kind: yaml.SequenceNode}
		for _, item := range v {
			child, err := orderedNode(item, itemKeys)
			if err != nil {
				return nil, err
			}
			node.Content = append(node.Content, child)
		}
		return node, nil
	default:
		node := &yaml.Node{}
		if err := node.Encode(v); err != nil {
			return nil, err
		}
		return node, nil
	}
}
