package engine

import (
	"fmt"
	"slices"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Mesh outbounds are only ever outbounds here (docs/mesh.md §4). The core runs as SYSTEM
// on Windows and in the system extension on macOS, so an option that lets other devices
// of the mesh network leave through this machine, reach its LAN or connect to it, would
// open the user's network to them. EasyTier has five; Tailscale's exit-node uses another
// device's exit, and ZeroTier has none.

// easyTierServing are EasyTier's options that serve other devices: being their exit,
// routing them into local subnets, announcing listeners to them, and listening for their
// connections. no-listener: false is the fifth: it listens on every interface.
var easyTierServing = []string{"enable-exit-node", "proxy-networks", "mapped-listeners", "listeners"}

// easyTierNoListener is the option that, when false, has EasyTier listen on mihomo's default
// listener, tcp://0.0.0.0:11010 (component/easytier/toml.go).
const easyTierNoListener = "no-listener"

// easyTierGuard turns the same options off in a provider's own override expressions,
// which mihomo applies to every proxy the provider loads, after its exclusions: the only
// hold on proxies a remote provider fetches later, and on expressions that make an
// EasyTier outbound of something else. It runs after the provider's own expressions.
var easyTierGuard = []any{
	`(select(.type == "easytier") | .enable-exit-node) = false`,
	`(select(.type == "easytier") | .proxy-networks) = []`,
	`(select(.type == "easytier") | .mapped-listeners) = []`,
	`(select(.type == "easytier") | .listeners) = []`,
	`(select(.type == "easytier") | .no-listener) = true`,
}

// meshRestriction is an option the pipeline turned off: on the outbound name, in the
// configuration or in a provider's payload.
type meshRestriction struct {
	name, option string
}

// restrictMesh turns off what lets other devices use the machine, in every EasyTier
// outbound the configuration holds and in what its providers load.
func restrictMesh(doc map[string]any) ([]meshRestriction, error) {
	var restricted []meshRestriction
	restrict := func(raw any, where string) error {
		if raw == nil {
			return nil
		}
		items, ok := raw.([]any)
		if !ok {
			return fmt.Errorf("%s must be a list", where)
		}
		for _, item := range items {
			proxy, ok := item.(map[string]any)
			if !ok || proxy["type"] != "easytier" {
				continue
			}
			name, _ := proxy["name"].(string)
			for _, option := range easyTierServing {
				if serving(proxy[option]) {
					restricted = append(restricted, meshRestriction{name, option})
				}
				delete(proxy, option)
			}
			if value, ok := proxy[easyTierNoListener]; ok && value != true {
				restricted = append(restricted, meshRestriction{name, easyTierNoListener})
			}
			// mihomo then wants peers to connect to; an outbound without them is refused.
			proxy[easyTierNoListener] = true
			proxy["enable-exit-node"] = false
		}
		return nil
	}
	if err := restrict(doc["proxies"], "proxies"); err != nil {
		return nil, err
	}
	providers, _ := doc["proxy-providers"].(map[string]any)
	for name, raw := range providers {
		// checkProviderPaths has made sure each provider is a mapping.
		provider := raw.(map[string]any)
		if err := restrict(provider["payload"], "proxy-providers."+name+".payload"); err != nil {
			return nil, err
		}
		if err := guardProvider(provider, name); err != nil {
			return nil, err
		}
	}
	return restricted, nil
}

// guardProvider appends easyTierGuard to a provider whose proxies the pipeline cannot
// see or that rewrites them: one that loads them from a URL or file, or that has
// override expressions of its own.
func guardProvider(provider map[string]any, name string) error {
	var override map[string]any
	switch raw := provider["override"].(type) {
	case nil:
	case map[string]any:
		override = raw
	default:
		return fmt.Errorf("proxy-providers.%s.override must be a mapping", name)
	}
	var expressions []any
	switch raw := override["override-expr"].(type) {
	case nil:
	case []any:
		expressions = raw
	default:
		return fmt.Errorf("proxy-providers.%s.override.override-expr must be a list", name)
	}
	if provider["type"] == "inline" && len(expressions) == 0 {
		return nil
	}
	if override == nil {
		override = map[string]any{}
		provider["override"] = override
	}
	override["override-expr"] = append(slices.Clone(expressions), easyTierGuard...)
	return nil
}

// serving is an option's value that turns it on: true, or a list with entries.
func serving(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case []any:
		return len(v) != 0
	case string:
		return v != "" && v != "false"
	}
	return true
}

// meshWarnings tells the composition's report which options went off, naming each
// outbound by its mesh id when the pool brought it, by its name otherwise.
func meshWarnings(report *controlpb.CompositionReport, restricted []meshRestriction) {
	for _, r := range restricted {
		id := r.name
		for _, mesh := range report.GetMesh() {
			if mesh.GetName() == r.name {
				id = mesh.GetId()
			}
		}
		report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
			EntryId: id,
			Reason:  compose.ReasonMeshRestricted,
			Target:  r.option,
		})
	}
}
