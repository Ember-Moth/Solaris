package compose

import (
	"errors"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/warp"
)

// warpDNS resolves the names that go through WARP inside its tunnel, so the node never
// sees the queries (docs/exits.md §3.1).
var warpDNS = []any{"1.1.1.1", "2606:4700:4700::1111"}

// warpName is the WARP exit's outbound, a name no group or node may take.
func warpName(locale string) string {
	if locale == localeEN {
		return "WARP exit"
	}
	return "WARP 出口"
}

// warpOutbound is the WARP exit as mihomo's masque or wireguard outbound, dialing out
// through the plan's via group (docs/exits.md §3.3). The fields follow the pinned
// mihomo's MasqueOption and WireGuardOption.
func (c *composer) warpOutbound(report *controlpb.CompositionReport) (map[string]any, error) {
	account := c.warp
	server, port, err := warp.SplitEndpoint(account.GetEndpoint())
	if err != nil {
		return nil, err
	}
	switch {
	case account.GetPrivateKey() == "" || account.GetPeerPublicKey() == "":
		return nil, errors.New("the device has no keys")
	case account.GetAddressV4() == "" && account.GetAddressV6() == "":
		return nil, errors.New("the device has no addresses")
	}
	outbound := map[string]any{
		"name":               warpName(c.plan.Locale),
		"type":               "masque",
		"server":             server,
		"port":               port,
		"private-key":        account.GetPrivateKey(),
		"public-key":         account.GetPeerPublicKey(),
		"mtu":                1280,
		"udp":                true,
		"remote-dns-resolve": true,
		"dns":                warpDNS,
		"dialer-proxy":       c.via(report),
	}
	if v4 := account.GetAddressV4(); v4 != "" {
		outbound["ip"] = v4
	}
	if v6 := account.GetAddressV6(); v6 != "" {
		outbound["ipv6"] = v6
	}
	switch account.GetProtocol() {
	case controlpb.WarpProtocol_WARP_PROTOCOL_UNSPECIFIED, controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2:
		// HTTP/2 needs only TCP from the node; without network, mihomo speaks HTTP/3.
		outbound["network"] = "h2"
	case controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H3:
	case controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD:
		outbound["type"] = "wireguard"
		if clientID := account.GetClientId(); clientID != "" {
			reserved, err := warp.Reserved(clientID)
			if err != nil {
				return nil, err
			}
			outbound["reserved"] = []any{reserved[0], reserved[1], reserved[2]}
		}
	default:
		return nil, errors.New("unknown protocol " + account.GetProtocol().String())
	}
	return outbound, nil
}

// via is the name of the group the WARP exit dials through. A group without members
// cannot carry it, so the exit goes through "proxy" meanwhile and the report says so;
// that changes the hop, not who sees what.
func (c *composer) via(report *controlpb.CompositionReport) string {
	id := c.plan.Egress.Warp.Via
	if id == "" {
		id = GroupProxy
	}
	if g, ok := c.byID[id]; ok && !g.empty() {
		return g.name
	}
	report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
		EntryId: EgressWarp,
		Reason:  ReasonViaEmpty,
		Target:  id,
	})
	return c.byID[GroupProxy].name
}
