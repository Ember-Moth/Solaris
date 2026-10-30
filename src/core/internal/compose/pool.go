package compose

import (
	"bytes"
	"errors"
	"fmt"
	"strconv"

	"github.com/dlclark/regexp2"
	"github.com/metacubex/mihomo/common/convert"
	"github.com/metacubex/mihomo/common/yaml"
	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Source is one subscription in the pool.
type Source struct {
	ID   string
	Name string
	YAML []byte
}

// node is one proxy of the pool.
type node struct {
	name     string // in the generated configuration
	original string // in the subscription
	source   int    // index into the sources
	kind     string // mihomo adapter type
	region   string
	mapping  map[string]any
}

// subscription is what a source holds: its proxies and, for a mihomo profile, the
// profile itself. Share links have no profile.
type subscription struct {
	proxies []map[string]any
	profile map[string]any
}

// parseSubscription reads a subscription the way mihomo's proxy providers do: a YAML
// mapping with proxies, or else share links, one per line or base64-encoded.
func parseSubscription(data []byte) (*subscription, error) {
	if len(bytes.TrimSpace(data)) == 0 {
		return nil, errors.New("the subscription is empty")
	}
	var profile map[string]any
	yamlErr := yaml.Unmarshal(data, &profile)
	if yamlErr == nil && profile != nil {
		proxies, err := proxyMappings(profile["proxies"])
		if err != nil {
			return nil, err
		}
		return &subscription{proxies: proxies, profile: profile}, nil
	}
	proxies, err := convert.ConvertsV2Ray(data)
	if err != nil {
		if yamlErr == nil {
			yamlErr = errors.New("not a YAML mapping")
		}
		return nil, fmt.Errorf("neither a mihomo profile (%w) nor share links (%w)", yamlErr, err)
	}
	return &subscription{proxies: proxies}, nil
}

func proxyMappings(raw any) ([]map[string]any, error) {
	if raw == nil {
		return nil, nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil, errors.New("proxies must be a list")
	}
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		// mihomo skips entries that are not mappings or have no name.
		if mapping, ok := item.(map[string]any); ok {
			out = append(out, mapping)
		}
	}
	return out, nil
}

// named yields the proxies with a name, each name once: a provider keeps the first proxy
// of a name and drops the rest.
func (s *subscription) named(yield func(name string, mapping map[string]any) bool) {
	seen := map[string]bool{}
	for _, mapping := range s.proxies {
		name, _ := mapping["name"].(string)
		if name == "" || seen[name] {
			continue
		}
		seen[name] = true
		if !yield(name, mapping) {
			return
		}
	}
}

func (s *subscription) count(key string) int {
	items, _ := s.profile[key].([]any)
	return len(items)
}

// adapterTypes names proxy types as mihomo's adapters report them (Proxy.type).
var adapterTypes = map[string]C.AdapterType{
	"ss": C.Shadowsocks, "ssr": C.ShadowsocksR, "socks5": C.Socks5, "http": C.Http,
	"vmess": C.Vmess, "vless": C.Vless, "snell": C.Snell, "trojan": C.Trojan,
	"hysteria": C.Hysteria, "hysteria2": C.Hysteria2, "wireguard": C.WireGuard, "tuic": C.Tuic,
	"shadowquic": C.ShadowQuic, "gost-relay": C.GostRelay, "direct": C.Direct, "dns": C.Dns,
	"reject": C.Reject, "rematch": C.Rematch, "ssh": C.Ssh, "mieru": C.Mieru, "anytls": C.AnyTLS,
	"sudoku": C.Sudoku, "masque": C.Masque, "trusttunnel": C.TrustTunnel, "openvpn": C.OpenVPN,
	"tailscale": C.Tailscale, "zerotier": C.ZeroTier, "easytier": C.EasyTier,
}

func adapterType(mapping map[string]any) string {
	kind, _ := mapping["type"].(string)
	if adapter, ok := adapterTypes[kind]; ok {
		return adapter.String()
	}
	return kind
}

// infoFilter matches information nodes; a nil filter matches nothing.
type infoFilter struct{ re *regexp2.Regexp }

func newInfoFilter(pattern string) (infoFilter, error) {
	if pattern == "" {
		return infoFilter{}, nil
	}
	re, err := regexp2.Compile(pattern, regexp2.None)
	if err != nil {
		return infoFilter{}, fmt.Errorf("info filter %q: %w", pattern, err)
	}
	return infoFilter{re}, nil
}

func (f infoFilter) matches(name string) bool {
	if f.re == nil {
		return false
	}
	found, _ := f.re.MatchString(name)
	return found
}

// Inspect describes a subscription for the UI (InspectSource): its nodes with their
// regions, and how many groups and rules it brings.
func Inspect(data []byte, infoPattern string) (*controlpb.InspectSourceResponse, error) {
	filter, err := newInfoFilter(infoPattern)
	if err != nil {
		return nil, err
	}
	parsed, err := parseSubscription(data)
	if err != nil {
		return nil, err
	}
	out := &controlpb.InspectSourceResponse{
		GroupCount: uint32(parsed.count("proxy-groups")),
		RuleCount:  uint32(parsed.count("rules")),
	}
	out.HasRules = out.RuleCount > 0
	for name, mapping := range parsed.named {
		out.Nodes = append(out.Nodes, &controlpb.SourceNode{
			Name:   name,
			Type:   adapterType(mapping),
			Region: RegionOf(name),
			Info:   filter.matches(name),
		})
	}
	return out, nil
}

// buildPool reads every source into one list of nodes. Information nodes stay out; a
// node whose name is taken, by an earlier source or by a name in taken, gets " · " and
// its source's name, and a number if that is taken too. Regions come from the names the
// subscriptions wrote, so a rename never moves a node.
func buildPool(sources []Source, filter infoFilter, taken map[string]bool) ([]*node, error) {
	var pool []*node
	for index, source := range sources {
		parsed, err := parseSubscription(source.YAML)
		if err != nil {
			return nil, fmt.Errorf("subscription %q: %w", source.Name, err)
		}
		for original, mapping := range parsed.named {
			if filter.matches(original) {
				continue
			}
			name := original
			if taken[name] {
				name = original + " · " + source.Name
				for n := 2; taken[name]; n++ {
					name = original + " · " + source.Name + " " + strconv.Itoa(n)
				}
			}
			taken[name] = true
			renamed := make(map[string]any, len(mapping))
			for key, value := range mapping {
				renamed[key] = value
			}
			renamed["name"] = name
			pool = append(pool, &node{
				name:     name,
				original: original,
				source:   index,
				kind:     adapterType(mapping),
				region:   RegionOf(original),
				mapping:  renamed,
			})
		}
	}
	return pool, nil
}
