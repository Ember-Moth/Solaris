package compose

import (
	"errors"
	"fmt"
	"net/netip"
	"net/url"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// The mesh networks the user added in the app (docs/mesh.md §10.3): each one an outbound of
// its own, generated here from Composition.Mesh, that keeps its state in the core's home.

// MeshStateRoot is the directory of the core's home that holds a directory per network.
const MeshStateRoot = "mesh"

var (
	networkIDPattern  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	zeroTierIDPattern = regexp.MustCompile(`^[0-9A-Fa-f]{16}$`)
)

// NetworkStateDir is where the network keeps its state, relative to the core's home.
func NetworkStateDir(id string) string { return path.Join(MeshStateRoot, id) }

// IsNetworkID says whether id may name a network: it becomes a directory name.
func IsNetworkID(id string) bool { return networkIDPattern.MatchString(id) }

// networkNodes checks the networks and makes each an outbound, in order. A network takes
// its own name unless a group, the WARP exit or an earlier network has it; then it gets
// " · " and its type, and a number if that is taken too. The names go into taken, before
// the pool's.
func networkNodes(networks []*controlpb.MeshNetwork, taken map[string]bool) ([]*node, error) {
	seen := map[string]bool{}
	var nodes []*node
	for _, network := range networks {
		label := fmt.Sprintf("mesh network %q", network.GetName())
		id := network.GetId()
		switch {
		case !IsNetworkID(id):
			return nil, fmt.Errorf("%s: the id %q is not letters, digits, - and _", label, id)
		case seen[id]:
			return nil, fmt.Errorf("%s: the id %q appears twice", label, id)
		}
		seen[id] = true
		if err := checkName(network.GetName()); err != nil {
			return nil, fmt.Errorf("%s: %w", label, err)
		}
		if err := checkVia(network.GetVia()); err != nil {
			return nil, fmt.Errorf("%s: %w", label, err)
		}
		if strings.HasPrefix(network.GetName(), providerPrefix) {
			return nil, fmt.Errorf("%s: names starting with %q are the subscriptions'", label, providerPrefix)
		}
		mapping, kind, err := networkOutbound(network)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", label, err)
		}
		name := network.GetName()
		if taken[name] {
			name = network.GetName() + " · " + kind
			for n := 2; taken[name]; n++ {
				name = network.GetName() + " · " + kind + " " + strconv.Itoa(n)
			}
		}
		taken[name] = true
		mapping["name"] = name
		nodes = append(nodes, &node{
			name:     name,
			original: network.GetName(),
			source:   -1,
			id:       meshPrefix + id,
			kind:     kind,
			via:      network.GetVia(),
			mapping:  mapping,
		})
	}
	return nodes, nil
}

// networkOutbound is the network as mihomo's outbound, without its name and dialer-proxy;
// the fields follow the pinned mihomo's TailscaleOption, ZeroTierOption and EasyTierOption.
// It also gives the outbound's adapter type.
func networkOutbound(network *controlpb.MeshNetwork) (map[string]any, string, error) {
	stateDir := NetworkStateDir(network.GetId())
	switch kind := network.GetNetwork().(type) {
	case *controlpb.MeshNetwork_Tailscale:
		settings := kind.Tailscale
		outbound := map[string]any{
			"type":      "tailscale",
			"state-dir": stateDir,
			// Routes other devices announce reach through this outbound only; the system's
			// routes stay as they are.
			"accept-routes": true,
			"udp":           true,
		}
		if control := settings.GetControlUrl(); control != "" {
			parsed, err := url.Parse(control)
			if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" {
				return nil, "", fmt.Errorf("the control server %q is not an http or https URL", control)
			}
			outbound["control-url"] = control
		}
		setNonEmpty(outbound, "auth-key", settings.GetAuthKey())
		setNonEmpty(outbound, "hostname", settings.GetHostname())
		return outbound, C.Tailscale.String(), nil
	case *controlpb.MeshNetwork_Zerotier:
		settings := kind.Zerotier
		if !zeroTierIDPattern.MatchString(settings.GetNetworkId()) {
			return nil, "", fmt.Errorf("the network id %q is not 16 hex digits", settings.GetNetworkId())
		}
		outbound := map[string]any{
			"type":      "zerotier",
			"network":   strings.ToLower(settings.GetNetworkId()),
			"state-dir": stateDir,
			"udp":       true,
		}
		setNonEmpty(outbound, "identity-secret", settings.GetIdentitySecret())
		return outbound, C.ZeroTier.String(), nil
	case *controlpb.MeshNetwork_Easytier:
		settings := kind.Easytier
		switch {
		case strings.TrimSpace(settings.GetNetworkName()) == "":
			return nil, "", errors.New("the network has no name")
		case settings.GetNetworkSecret() == "":
			return nil, "", errors.New("the network has no secret")
		case len(settings.GetPeers()) == 0:
			return nil, "", errors.New("the network has no peers")
		}
		peers := make([]any, len(settings.GetPeers()))
		for i, peer := range settings.GetPeers() {
			peers[i] = peer
		}
		outbound := map[string]any{
			"type":           "easytier",
			"network-name":   settings.GetNetworkName(),
			"network-secret": settings.GetNetworkSecret(),
			"peers":          peers,
			"state-dir":      stateDir,
			// Only an outbound (docs/mesh.md §10.6): no peer connects to it, and it is no
			// one's exit.
			"no-listener":      true,
			"enable-exit-node": false,
			"udp":              true,
		}
		if address := settings.GetIpv4(); address != "" {
			if prefix, err := netip.ParsePrefix(address); err != nil || !prefix.Addr().Is4() {
				return nil, "", fmt.Errorf("the address %q is not IPv4 with a prefix length, such as 10.144.144.2/24", address)
			}
			outbound["ipv4"] = address
		} else {
			outbound["dhcp"] = true
		}
		setNonEmpty(outbound, "hostname", settings.GetHostname())
		return outbound, C.EasyTier.String(), nil
	}
	return nil, "", errors.New("the network has no type")
}

func setNonEmpty(mapping map[string]any, key, value string) {
	if value != "" {
		mapping[key] = value
	}
}

// networkVia is the name of the group the network dials through, or "" to dial directly.
// A group without members cannot carry it, so it goes through "proxy" meanwhile and the
// report says so, as the WARP exit does.
func (c *composer) networkVia(n *node, report *controlpb.CompositionReport) string {
	if n.via == "" {
		return ""
	}
	if g, ok := c.byID[n.via]; ok && !g.empty() {
		return g.name
	}
	report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
		EntryId: n.id,
		Reason:  ReasonViaEmpty,
		Target:  n.via,
	})
	return c.byID[GroupProxy].name
}

// checkVia accepts what a network may dial through: a group's id. A group the plan in use
// does not have is no error, since the network belongs to the device and not to a plan;
// networkVia passes it to "proxy" then.
func checkVia(via string) error {
	switch {
	case via == "":
		return nil
	case slices.Contains(outbounds, via), via == allRegions, via == EgressWarp,
		strings.HasPrefix(via, meshPrefix), strings.TrimSpace(via) != via:
		return fmt.Errorf("via %q is not a group", via)
	}
	return nil
}
