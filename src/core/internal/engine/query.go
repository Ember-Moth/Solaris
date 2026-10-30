package engine

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/metacubex/mihomo/adapter/outboundgroup"
	"github.com/metacubex/mihomo/component/profile/cachefile"
	"github.com/metacubex/mihomo/component/resolver"
	C "github.com/metacubex/mihomo/constant"
	P "github.com/metacubex/mihomo/constant/provider"
	"github.com/metacubex/mihomo/listener"
	T "github.com/metacubex/mihomo/tunnel"
	"github.com/metacubex/mihomo/tunnel/statistic"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const (
	defaultDelayTimeout = 5 * time.Second
	maxDelayTimeout     = time.Minute
	// unknownDelay is what mihomo reports for a proxy that is not alive or never tested.
	unknownDelay = 0xffff
	globalGroup  = "GLOBAL"
)

// Status describes the core. The host fills in what it owns, such as the system proxy.
func (e *Engine) Status() *controlpb.GetStatusResponse {
	e.mu.RLock()
	status := &controlpb.GetStatusResponse{
		State: e.state,
		Mode:  modeToProto(T.Mode()),
	}
	if !e.startedAt.IsZero() {
		status.StartedAtUnixMs = uint64(e.startedAt.UnixMilli())
	}
	if e.lastError != nil {
		status.LastError = &controlpb.Error{Code: "apply_failed", Message: e.lastError.Error()}
	}
	e.mu.RUnlock()
	if status.State == controlpb.RunState_RUN_STATE_RUNNING {
		status.MemoryBytes = statistic.DefaultManager.Memory()
		statistic.DefaultManager.Range(func(statistic.Tracker) bool {
			status.ConnectionCount++
			return true
		})
		status.TunEnabled = listener.GetTunConf().Enable
	}
	return status
}

func modeToProto(mode T.TunnelMode) controlpb.Mode {
	switch mode {
	case T.Rule:
		return controlpb.Mode_MODE_RULE
	case T.Global:
		return controlpb.Mode_MODE_GLOBAL
	case T.Direct:
		return controlpb.Mode_MODE_DIRECT
	}
	return controlpb.Mode_MODE_UNSPECIFIED
}

// SetMode switches the routing mode of the running core.
func (e *Engine) SetMode(mode controlpb.Mode) error {
	var target T.TunnelMode
	switch mode {
	case controlpb.Mode_MODE_RULE:
		target = T.Rule
	case controlpb.Mode_MODE_GLOBAL:
		target = T.Global
	case controlpb.Mode_MODE_DIRECT:
		target = T.Direct
	default:
		return fmt.Errorf("%w: unknown mode %v", ErrInvalidArgument, mode)
	}
	if !e.running() {
		return ErrNotRunning
	}
	T.SetMode(target)
	return nil
}

func groupOf(proxy C.Proxy) (outboundgroup.ProxyGroup, bool) {
	group, ok := proxy.Adapter().(outboundgroup.ProxyGroup)
	return group, ok
}

func selectable(proxy C.Proxy) bool {
	_, ok := proxy.Adapter().(outboundgroup.SelectAble)
	return ok
}

// ListGroups returns the proxy groups in configuration order, which mihomo keeps as the
// member order of GLOBAL; GLOBAL itself comes last. Hidden groups are left out.
func (e *Engine) ListGroups(sort controlpb.GroupSort, excludeNotSelectable bool) []*controlpb.ProxyGroup {
	if !e.running() {
		return nil
	}
	proxies := T.Proxies()
	var ordered []C.Proxy
	if global, ok := proxies[globalGroup]; ok {
		if group, ok := groupOf(global); ok {
			for _, member := range group.Proxies() {
				if proxy, ok := proxies[member.Name()]; ok {
					ordered = append(ordered, proxy)
				}
			}
			ordered = append(ordered, global)
		}
	}
	groups := make([]*controlpb.ProxyGroup, 0, len(ordered))
	for _, proxy := range ordered {
		group, ok := groupOf(proxy)
		if !ok || (group.Hidden() && proxy.Name() != globalGroup) {
			continue
		}
		if excludeNotSelectable && !selectable(proxy) {
			continue
		}
		groups = append(groups, groupToProto(proxy, group, sort))
	}
	return groups
}

func (e *Engine) GetGroup(name string, sort controlpb.GroupSort) (*controlpb.ProxyGroup, error) {
	if !e.running() {
		return nil, ErrNotRunning
	}
	proxy, group, err := findGroup(name)
	if err != nil {
		return nil, err
	}
	return groupToProto(proxy, group, sort), nil
}

func findGroup(name string) (C.Proxy, outboundgroup.ProxyGroup, error) {
	proxy, ok := T.Proxies()[name]
	if !ok {
		return nil, nil, fmt.Errorf("%w: no group named %q", ErrInvalidArgument, name)
	}
	group, ok := groupOf(proxy)
	if !ok {
		return nil, nil, fmt.Errorf("%w: %q is not a group", ErrInvalidArgument, name)
	}
	return proxy, group, nil
}

// testURL is the URL a group measures its members with. mihomo only exposes it through
// the group's JSON, and leaves it empty there when it is the default.
func testURL(group outboundgroup.ProxyGroup) string {
	var fields struct {
		TestURL string `json:"testUrl"`
	}
	if raw, err := group.MarshalJSON(); err == nil {
		_ = json.Unmarshal(raw, &fields)
	}
	if fields.TestURL == "" {
		return C.DefaultTestURL
	}
	return fields.TestURL
}

func groupToProto(proxy C.Proxy, group outboundgroup.ProxyGroup, sort controlpb.GroupSort) *controlpb.ProxyGroup {
	url := testURL(group)
	members := group.Proxies()
	out := &controlpb.ProxyGroup{
		Name:    proxy.Name(),
		Type:    proxy.Type().String(),
		Now:     group.Now(),
		TestUrl: url,
		Proxies: make([]*controlpb.Proxy, 0, len(members)),
	}
	for _, member := range members {
		entry := &controlpb.Proxy{Name: member.Name(), Type: member.Type().String()}
		if _, tested := member.ExtraDelayHistories()[url]; tested {
			// A failed test is 0; mihomo reports it as the largest delay.
			var value uint32
			if delay := member.LastDelayForTestUrl(url); delay != unknownDelay {
				value = uint32(delay)
			}
			entry.DelayMs = &value
		}
		out.Proxies = append(out.Proxies, entry)
	}
	switch sort {
	case controlpb.GroupSort_GROUP_SORT_NAME:
		slices.SortStableFunc(out.Proxies, func(a, b *controlpb.Proxy) int { return strings.Compare(a.Name, b.Name) })
	case controlpb.GroupSort_GROUP_SORT_DELAY:
		// Untested and failed proxies go last.
		slices.SortStableFunc(out.Proxies, func(a, b *controlpb.Proxy) int {
			return cmp.Compare(sortableDelay(a), sortableDelay(b))
		})
	}
	return out
}

func sortableDelay(proxy *controlpb.Proxy) uint64 {
	if proxy.GetDelayMs() == 0 {
		return 1 << 32
	}
	return uint64(*proxy.DelayMs)
}

// SelectProxy picks a group member and records it, so a restart keeps the choice (§3.3 #5).
// Groups that choose for themselves (url-test, fallback) are pinned to the member.
func (e *Engine) SelectProxy(groupName, proxyName string) error {
	if !e.running() {
		return ErrNotRunning
	}
	proxy, _, err := findGroup(groupName)
	if err != nil {
		return err
	}
	selector, ok := proxy.Adapter().(outboundgroup.SelectAble)
	if !ok {
		return fmt.Errorf("%w: %q does not take a selection", ErrInvalidArgument, groupName)
	}
	if err := selector.Set(proxyName); err != nil {
		return fmt.Errorf("%w: %w", ErrInvalidArgument, err)
	}
	cachefile.Cache().SetSelected(groupName, proxyName)
	return nil
}

// HealthCheck tests every member of a group, or of every group when name is empty.
// Like mihomo's own group delay test, it unpins url-test and fallback groups.
func (e *Engine) HealthCheck(ctx context.Context, name string) error {
	if !e.running() {
		return ErrNotRunning
	}
	var targets []C.Proxy
	if name == "" {
		for _, proxy := range T.Proxies() {
			if _, ok := groupOf(proxy); ok {
				targets = append(targets, proxy)
			}
		}
	} else {
		proxy, _, err := findGroup(name)
		if err != nil {
			return err
		}
		targets = append(targets, proxy)
	}
	ctx, cancel := context.WithTimeout(ctx, defaultDelayTimeout)
	defer cancel()
	var wg sync.WaitGroup
	for _, proxy := range targets {
		group, _ := groupOf(proxy)
		if pinned, ok := proxy.Adapter().(outboundgroup.SelectAble); ok && proxy.Type() != C.Selector {
			pinned.ForceSet("")
			cachefile.Cache().SetSelected(proxy.Name(), "")
		}
		wg.Go(func() { _, _ = group.URLTest(ctx, testURL(group), nil) })
	}
	wg.Wait()
	return nil
}

// TestDelay measures one proxy, configured directly or supplied by a provider.
func (e *Engine) TestDelay(ctx context.Context, name, url string, timeout time.Duration) (uint32, error) {
	if !e.running() {
		return 0, ErrNotRunning
	}
	proxy, ok := findProxy(name)
	if !ok {
		return 0, fmt.Errorf("%w: no proxy named %q", ErrInvalidArgument, name)
	}
	if url == "" {
		url = C.DefaultTestURL
	}
	if timeout <= 0 {
		timeout = defaultDelayTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, min(timeout, maxDelayTimeout))
	defer cancel()
	delay, err := proxy.URLTest(ctx, url, nil)
	if err != nil {
		return 0, fmt.Errorf("delay test of %s: %w", name, err)
	}
	if delay == 0 {
		return 0, fmt.Errorf("delay test of %s failed", name)
	}
	return uint32(delay), nil
}

func findProxy(name string) (C.Proxy, bool) {
	if proxy, ok := T.Proxies()[name]; ok {
		return proxy, true
	}
	for _, provider := range T.Providers() {
		for _, proxy := range provider.Proxies() {
			if proxy.Name() == name {
				return proxy, true
			}
		}
	}
	return nil, false
}

// ListProviders returns the providers the profile declares. mihomo also wraps the
// proxies listed inline in each group in a compatible provider; those are internal.
func (e *Engine) ListProviders() []*controlpb.Provider {
	if !e.running() {
		return nil
	}
	var out []*controlpb.Provider
	for _, provider := range T.Providers() {
		if provider.VehicleType() == P.Compatible {
			continue
		}
		out = append(out, providerToProto(provider, controlpb.ProviderKind_PROVIDER_KIND_PROXY))
	}
	for _, provider := range T.RuleProviders() {
		out = append(out, providerToProto(provider, controlpb.ProviderKind_PROVIDER_KIND_RULE))
	}
	slices.SortFunc(out, func(a, b *controlpb.Provider) int {
		return cmp.Or(cmp.Compare(a.Kind, b.Kind), strings.Compare(a.Name, b.Name))
	})
	return out
}

func providerToProto(provider P.Provider, kind controlpb.ProviderKind) *controlpb.Provider {
	out := &controlpb.Provider{
		Name:        provider.Name(),
		Kind:        kind,
		VehicleType: provider.VehicleType().String(),
	}
	if updated, ok := provider.(interface{ UpdatedAt() time.Time }); ok {
		if at := updated.UpdatedAt(); !at.IsZero() {
			out.UpdatedAtUnixMs = uint64(at.UnixMilli())
		}
	}
	return out
}

func (e *Engine) UpdateProvider(kind controlpb.ProviderKind, name string) error {
	if !e.running() {
		return ErrNotRunning
	}
	var provider P.Provider
	var ok bool
	switch kind {
	case controlpb.ProviderKind_PROVIDER_KIND_PROXY:
		var proxyProvider P.ProxyProvider
		proxyProvider, ok = T.Providers()[name]
		ok = ok && proxyProvider.VehicleType() != P.Compatible
		provider = proxyProvider
	case controlpb.ProviderKind_PROVIDER_KIND_RULE:
		provider, ok = T.RuleProviders()[name]
	default:
		return fmt.Errorf("%w: unknown provider kind %v", ErrInvalidArgument, kind)
	}
	if !ok {
		return fmt.Errorf("%w: no provider named %q", ErrInvalidArgument, name)
	}
	if err := provider.Update(); err != nil {
		return fmt.Errorf("update provider %s: %w", name, err)
	}
	return nil
}

// ruleKeywords names mihomo's rule types as profiles write them; the core names them in
// CamelCase ("DstPort" for DST-PORT).
var ruleKeywords = map[C.RuleType]string{
	C.Domain:              "DOMAIN",
	C.DomainSuffix:        "DOMAIN-SUFFIX",
	C.DomainKeyword:       "DOMAIN-KEYWORD",
	C.DomainRegex:         "DOMAIN-REGEX",
	C.DomainWildcard:      "DOMAIN-WILDCARD",
	C.GEOSITE:             "GEOSITE",
	C.GEOIP:               "GEOIP",
	C.SrcGEOIP:            "SRC-GEOIP",
	C.IPASN:               "IP-ASN",
	C.SrcIPASN:            "SRC-IP-ASN",
	C.IPCIDR:              "IP-CIDR",
	C.SrcIPCIDR:           "SRC-IP-CIDR",
	C.IPSuffix:            "IP-SUFFIX",
	C.SrcIPSuffix:         "SRC-IP-SUFFIX",
	C.SrcPort:             "SRC-PORT",
	C.DstPort:             "DST-PORT",
	C.InPort:              "IN-PORT",
	C.InUser:              "IN-USER",
	C.InName:              "IN-NAME",
	C.InType:              "IN-TYPE",
	C.ProcessName:         "PROCESS-NAME",
	C.ProcessPath:         "PROCESS-PATH",
	C.ProcessNameRegex:    "PROCESS-NAME-REGEX",
	C.ProcessPathRegex:    "PROCESS-PATH-REGEX",
	C.ProcessNameWildcard: "PROCESS-NAME-WILDCARD",
	C.ProcessPathWildcard: "PROCESS-PATH-WILDCARD",
	C.RematchName:         "REMATCH-NAME",
	C.MATCH:               "MATCH",
	C.RuleSet:             "RULE-SET",
	C.Network:             "NETWORK",
	C.DSCP:                "DSCP",
	C.Uid:                 "UID",
	C.SubRules:            "SUB-RULE",
	C.AND:                 "AND",
	C.OR:                  "OR",
	C.NOT:                 "NOT",
}

// ruleKeywordsByName finds the keyword from the name a connection records.
var ruleKeywordsByName = func() map[string]string {
	byName := make(map[string]string, len(ruleKeywords))
	for ruleType, keyword := range ruleKeywords {
		byName[ruleType.String()] = keyword
	}
	return byName
}()

// ruleKeyword is the profile's spelling of a rule type; an unknown one stays as it is.
func ruleKeyword(name string) string {
	return cmp.Or(ruleKeywordsByName[name], name)
}

func (e *Engine) ListRules() []*controlpb.Rule {
	if !e.running() {
		return nil
	}
	rules := T.Rules()
	entries := e.ruleEntries()
	out := make([]*controlpb.Rule, 0, len(rules))
	for i, rule := range rules {
		out = append(out, &controlpb.Rule{
			Type:    ruleKeyword(rule.RuleType().String()),
			Payload: rule.Payload(),
			Proxy:   rule.Adapter(),
			EntryId: entries.index(i),
		})
	}
	return out
}

func (e *Engine) CloseConnection(id string) error {
	if !e.running() {
		return ErrNotRunning
	}
	connection := statistic.DefaultManager.Get(id)
	if connection == nil {
		return fmt.Errorf("%w: no connection %q", ErrInvalidArgument, id)
	}
	return connection.Close()
}

func (e *Engine) CloseAllConnections() error {
	if !e.running() {
		return ErrNotRunning
	}
	statistic.DefaultManager.Range(func(c statistic.Tracker) bool {
		_ = c.Close()
		return true
	})
	return nil
}

func (e *Engine) FlushFakeIP() error {
	if !e.running() {
		return ErrNotRunning
	}
	return resolver.FlushFakeIP()
}

func (e *Engine) FlushDNSCache() error {
	if !e.running() {
		return ErrNotRunning
	}
	resolver.ClearCache()
	return nil
}

// Traffic samples the rates of the last second and the totals since start-up.
func (e *Engine) Traffic() *controlpb.TrafficSample {
	up, down := statistic.DefaultManager.Now()
	upTotal, downTotal := statistic.DefaultManager.Total()
	return &controlpb.TrafficSample{
		UpBytesPerSecond:   nonNegative(up),
		DownBytesPerSecond: nonNegative(down),
		UpTotalBytes:       nonNegative(upTotal),
		DownTotalBytes:     nonNegative(downTotal),
	}
}

func nonNegative(n int64) uint64 {
	return uint64(max(n, 0))
}

// Connections snapshots the open connections, oldest first.
func (e *Engine) Connections() *controlpb.ConnectionSnapshot {
	snapshot := &controlpb.ConnectionSnapshot{}
	entries := e.ruleEntries()
	statistic.DefaultManager.Range(func(c statistic.Tracker) bool {
		connection := connectionToProto(c.Info())
		connection.RuleEntryId = entries.connection(c.Info())
		snapshot.Connections = append(snapshot.Connections, connection)
		return true
	})
	slices.SortStableFunc(snapshot.Connections, func(a, b *controlpb.Connection) int {
		return cmp.Or(cmp.Compare(a.StartedAtUnixMs, b.StartedAtUnixMs), strings.Compare(a.Id, b.Id))
	})
	return snapshot
}

func connectionToProto(info *statistic.TrackerInfo) *controlpb.Connection {
	out := &controlpb.Connection{
		Id:              info.UUID.String(),
		UploadBytes:     nonNegative(info.UploadTotal.Load()),
		DownloadBytes:   nonNegative(info.DownloadTotal.Load()),
		StartedAtUnixMs: uint64(info.Start.UnixMilli()),
		Rule:            ruleKeyword(info.Rule),
		RulePayload:     info.RulePayload,
		Chains:          slices.Clone([]string(info.Chain)),
	}
	if metadata := info.Metadata; metadata != nil {
		out.Network = metadata.NetWork.String()
		out.Source = metadata.SourceAddress()
		out.Destination = net.JoinHostPort(metadata.DstIP.String(), strconv.Itoa(int(metadata.DstPort)))
		out.Host = cmp.Or(metadata.Host, metadata.SniffHost)
		out.Process = cmp.Or(metadata.Process, metadata.ProcessPath)
	}
	return out
}
