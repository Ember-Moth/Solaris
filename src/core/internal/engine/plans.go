package engine

import (
	"context"
	"fmt"
	"net/netip"
	"slices"
	"strings"

	"github.com/metacubex/mihomo/component/resolver"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/listener"
	T "github.com/metacubex/mihomo/tunnel"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata"
)

// The routing editor's methods (docs/routing.md §8.2, §8.3). They only read: a draft is
// built on the side as Validate builds it and never applied, so none of them needs a
// running core or changes what it runs.

// PreviewGroup works out the members a group of a draft plan gets with the pool.
func (e *Engine) PreviewGroup(composition compose.Composition, groupID string) (*controlpb.PreviewGroupResponse, error) {
	preview, err := compose.Preview(composition, groupID)
	if err != nil {
		return nil, invalidConfig(err)
	}
	return preview, nil
}

// ConvertToPlan makes a plan from a subscription's or a configuration's groups and rules.
// GEOSITE and GEOIP rules with a category the core's GEO data lacks are left out.
func (e *Engine) ConvertToPlan(source []byte, options compose.Conversion) (*controlpb.ConvertToPlanResponse, error) {
	options.Categories = e.categories()
	converted, err := compose.Convert(source, options)
	if err != nil {
		return nil, invalidConfig(err)
	}
	return converted, nil
}

// categories are the GEO data's categories, or nil lists, which check nothing, for a kind
// the home has no data of.
func (e *Engine) categories() compose.Categories {
	var categories compose.Categories
	if sites, err := geodata.Categories(C.Path.GeoSite()); err == nil && len(sites.Names) != 0 {
		categories.Geosite = sites.Names
	}
	if ips, err := geodata.Categories(C.Path.GeoIP()); err == nil && len(ips.Names) != 0 {
		categories.Geoip = ips.Names
	}
	return categories
}

// ExportPlan writes a plan as YAML: proxy-groups and rules to share, or the whole
// configuration the core would run with profile.
func (e *Engine) ExportPlan(profile Profile, format controlpb.ExportFormat) ([]byte, error) {
	if profile.Composition == nil {
		return nil, fmt.Errorf("%w: export_plan needs a composition", ErrInvalidArgument)
	}
	switch format {
	case controlpb.ExportFormat_EXPORT_FORMAT_UNSPECIFIED, controlpb.ExportFormat_EXPORT_FORMAT_SHARE:
		exported, err := compose.Export(*profile.Composition)
		if err != nil {
			return nil, invalidConfig(err)
		}
		return exported, nil
	case controlpb.ExportFormat_EXPORT_FORMAT_GENERATED:
		if e.tunMode == TunNone {
			// The configuration shows the host's way of running it, which never has a TUN.
			profile.Options.TUN = false
		}
		admitted, err := e.draft(profile)
		if err != nil {
			return nil, err
		}
		reduceNodes(admitted.doc)
		generated, err := compose.MarshalOrdered(admitted.doc)
		if err != nil {
			return nil, invalidConfig(err)
		}
		return generated, nil
	}
	return nil, fmt.Errorf("%w: unknown export format %v", ErrInvalidArgument, format)
}

// shownNodeKeys are what the generated configuration shows of a node: enough to tell
// nodes apart, nothing that lets someone else use them.
var shownNodeKeys = []string{"name", "type", "server", "port"}

// reduceNodes keeps the shown keys of every node in the configuration and its providers.
func reduceNodes(doc map[string]any) {
	reduce := func(raw any) {
		items, _ := raw.([]any)
		for i, item := range items {
			node, ok := item.(map[string]any)
			if !ok {
				continue
			}
			shown := map[string]any{}
			for _, key := range shownNodeKeys {
				if value, ok := node[key]; ok {
					shown[key] = value
				}
			}
			items[i] = shown
		}
	}
	reduce(doc["proxies"])
	providers, _ := doc["proxy-providers"].(map[string]any)
	for _, raw := range providers {
		if provider, ok := raw.(map[string]any); ok {
			reduce(provider["payload"])
		}
	}
}

// draft runs a configuration through the admission pipeline, as Validate does.
func (e *Engine) draft(profile Profile) (*admission, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return nil, err
	}
	return e.admit(profile)
}

// MatchRequest is a connection to test: to Host, a domain or an IP address, on Port.
type MatchRequest struct {
	// Draft is the configuration to match against; nil for the running one.
	Draft *Profile
	Host  string
	Port  uint16
	UDP   bool
}

// MatchRule finds the rule a connection would match, with mihomo's rule matching: the
// first rule that matches and whose target exists, skipping targets that pass or that
// cannot carry UDP, as the tunnel does (tunnel.match). An IP rule resolves a domain once
// with the core's resolver; a process rule never matches, as no process is behind the test.
func (e *Engine) MatchRule(request MatchRequest) (*controlpb.MatchRuleResponse, error) {
	host := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(request.Host)), ".")
	if host == "" || strings.ContainsAny(host, " /,") {
		return nil, fmt.Errorf("%w: %q is neither a domain nor an IP address", ErrInvalidArgument, request.Host)
	}
	var (
		rules   []C.Rule
		proxies map[string]C.Proxy
		entries *ruleEntries
	)
	if request.Draft == nil {
		if !e.running() {
			return nil, ErrNotRunning
		}
		rules, proxies, entries = T.Rules(), T.Proxies(), e.ruleEntries()
	} else {
		admitted, err := e.draft(*request.Draft)
		if err != nil {
			return nil, err
		}
		rules, proxies, entries = admitted.cfg.Rules, admitted.cfg.Proxies, admitted.entries
	}
	// The inbound is the TUN when it runs, and otherwise the mixed port as a browser's
	// CONNECT reaches it.
	metadata := &C.Metadata{NetWork: C.TCP, Type: C.HTTPS, DstPort: request.Port}
	if request.UDP {
		metadata.NetWork = C.UDP
	}
	if listener.GetTunConf().Enable {
		metadata.Type = C.TUN
	}
	if ip, err := netip.ParseAddr(host); err == nil {
		metadata.DstIP = ip.Unmap()
	} else {
		metadata.Host = host
	}
	out := &controlpb.MatchRuleResponse{}
	index, rule := matchRules(rules, proxies, metadata, out)
	if rule == nil {
		out.Chain = []string{"DIRECT"}
		return out, nil
	}
	out.Matched = true
	out.Index = uint32(index)
	out.Rule = &controlpb.Rule{
		Type:    ruleKeyword(rule.RuleType().String()),
		Payload: rule.Payload(),
		Proxy:   rule.Adapter(),
		EntryId: entries.index(index),
	}
	out.Chain = e.chain(rule.Adapter(), metadata)
	return out, nil
}

// matchRules is tunnel.match for one connection, without rematching; out records the
// address a resolving rule looked up.
func matchRules(rules []C.Rule, proxies map[string]C.Proxy, metadata *C.Metadata, out *controlpb.MatchRuleResponse) (int, C.Rule) {
	resolved := false
	if node, ok := resolver.DefaultHosts.Search(metadata.Host, false); ok {
		metadata.DstIP, _ = node.RandIP()
		resolved = true
	}
	helper := C.RuleMatchHelper{
		ResolveIP: func() {
			if resolved || metadata.Host == "" || metadata.Resolved() {
				return
			}
			resolved = true
			ctx, cancel := context.WithTimeout(context.Background(), resolver.DefaultDNSTimeout)
			defer cancel()
			if ip, err := resolver.ResolveIP(ctx, metadata.Host); err == nil {
				metadata.DstIP = ip
				out.ResolvedIp = ip.String()
			}
		},
		CheckPassRule: func(name string) bool {
			for proxy := proxies[name]; proxy != nil; proxy = proxy.Unwrap(metadata, false) {
				if proxy.Type() == C.PassRule {
					return true
				}
			}
			return false
		},
	}
next:
	for i, rule := range rules {
		matched, name := rule.Match(metadata, helper)
		if !matched {
			continue
		}
		adapter, ok := proxies[name]
		if !ok {
			continue
		}
		for proxy := adapter; proxy != nil; proxy = proxy.Unwrap(metadata, false) {
			if proxy.Type() == C.Pass {
				continue next
			}
		}
		if metadata.NetWork == C.UDP && !adapter.SupportUDP() {
			continue
		}
		return i, rule
	}
	return 0, nil
}

// chain is the outbound chain from the node to target as the running core would take it
// now, in Connection.chains order; the target alone when the core does not run it.
func (e *Engine) chain(target string, metadata *C.Metadata) []string {
	proxy, ok := T.Proxies()[target]
	if !ok || !e.running() && !slices.Contains([]string{"DIRECT", "REJECT", "REJECT-DROP"}, target) {
		return []string{target}
	}
	var chain []string
	for ; proxy != nil; proxy = proxy.Unwrap(metadata, false) {
		chain = append(chain, proxy.Name())
	}
	slices.Reverse(chain)
	return chain
}
