package compose

import (
	"cmp"
	"errors"
	"fmt"
	"strings"

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
		"dialer-proxy":       c.via(EgressWarp, c.plan.Egress.Warp.Via, report),
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

// via is the name of the group the exit dials through, given as the plan's group id. A
// group without members cannot carry it, so the exit goes through "proxy" meanwhile and the
// report says so; that changes the hop, not who sees what.
func (c *composer) via(exit, id string, report *controlpb.CompositionReport) string {
	if id == "" {
		id = GroupProxy
	}
	if g, ok := c.byID[id]; ok && !g.empty() {
		return g.name
	}
	report.Warnings = append(report.Warnings, &controlpb.CompositionWarning{
		EntryId: exit,
		Reason:  ReasonViaEmpty,
		Target:  id,
	})
	return c.byID[GroupProxy].name
}

// vpnGateDNS resolves the names that go through VPN Gate inside the tunnel: sites answer for
// the exit's country, and the node never sees the queries.
var vpnGateDNS = []any{"1.1.1.1", "8.8.8.8"}

// The VPN Gate exit's group checks its servers quickly, since a failed connection is not
// tried again elsewhere (docs/exits.md §5.3), and only while something goes through it.
const (
	vpnGateInterval       = 120  // seconds
	vpnGateTimeout        = 5000 // milliseconds
	vpnGateMaxFailedTimes = 2
	// An OpenVPN handshake through the node may outlast a delay test; the outbound goes on
	// with it in the background, so the next test finds the tunnel up.
	vpnGateHandshakeTimeout = 15 // seconds
	// Keepalive as VPN Gate's servers ask of OpenVPN's own client, so a dead tunnel restarts.
	vpnGatePing        = 10 // seconds
	vpnGatePingRestart = 60 // seconds
)

// vpnGateName is the VPN Gate exit's group, a name no group or node may take.
func vpnGateName(locale string) string {
	if locale == localeEN {
		return "VPN Gate exit"
	}
	return "VPN Gate 出口"
}

// vpnGateServerName names a server's outbound by its country and the list's host name.
func vpnGateServerName(server *controlpb.VpnGateServer) string {
	return "VPN Gate " + server.GetCountry() + " " + server.GetHostName()
}

// checkVpnGate keeps the servers usable as outbounds: each once, with a name a rule can hold
// and its OpenVPN options. mihomo checks the options themselves as it loads them.
func checkVpnGate(servers []*controlpb.VpnGateServer) error {
	seen := map[string]bool{}
	for _, server := range servers {
		host := server.GetHostName()
		switch {
		case host == "" || strings.ContainsAny(host, ", \r\n") || strings.ContainsAny(server.GetCountry(), ", \r\n"):
			return fmt.Errorf("server %q has no usable name", host)
		case seen[host]:
			return fmt.Errorf("server %q appears twice", host)
		case server.GetOpenvpn() == nil:
			return fmt.Errorf("server %q has no OpenVPN options", host)
		}
		seen[host] = true
	}
	return nil
}

// vpnGateExit is the VPN Gate exit (docs/exits.md §5.3): an openvpn outbound per server,
// dialing out through the plan's via group, and the group that rules name. "per-site" is a
// load-balance group that hashes each site to a server, so a site keeps its address and
// only the sites of a server that failed move; "single" uses the first server that works.
// Both leave out the servers their health check found dead.
func (c *composer) vpnGateExit(report *controlpb.CompositionReport) ([]any, map[string]any) {
	settings := c.plan.Egress.VpnGate
	via := c.via(EgressVpnGate, settings.Via, report)
	outbounds := make([]any, 0, len(c.vpnGate))
	names := make([]any, 0, len(c.vpnGate))
	for _, server := range c.vpnGate {
		options := server.GetOpenvpn()
		outbound := map[string]any{
			"name":               vpnGateServerName(server),
			"type":               "openvpn",
			"server":             options.GetServer(),
			"port":               int(options.GetPort()),
			"proto":              cmp.Or(options.GetProto(), "tcp"),
			"cipher":             options.GetCipher(),
			"auth":               options.GetAuth(),
			"ca":                 options.GetCa(),
			"handshake-timeout":  vpnGateHandshakeTimeout,
			"ping":               vpnGatePing,
			"ping-restart":       vpnGatePingRestart,
			"udp":                true,
			"remote-dns-resolve": true,
			"dns":                vpnGateDNS,
			"dialer-proxy":       via,
		}
		if ciphers := options.GetDataCiphers(); len(ciphers) != 0 {
			list := make([]any, len(ciphers))
			for i, cipher := range ciphers {
				list[i] = cipher
			}
			outbound["data-ciphers"] = list
		}
		for key, value := range map[string]string{
			"comp-lzo":      options.GetCompLzo(),
			"cert":          options.GetCert(),
			"key":           options.GetKey(),
			"tls-auth":      options.GetTlsAuth(),
			"key-direction": options.GetKeyDirection(),
		} {
			if value != "" {
				outbound[key] = value
			}
		}
		outbounds = append(outbounds, outbound)
		names = append(names, outbound["name"])
	}
	group := map[string]any{
		"name":             vpnGateName(c.plan.Locale),
		"type":             "load-balance",
		"strategy":         "consistent-hashing",
		"proxies":          names,
		"interval":         vpnGateInterval,
		"timeout":          vpnGateTimeout,
		"max-failed-times": vpnGateMaxFailedTimes,
		"lazy":             true,
	}
	if settings.Mode == VpnGateSingle {
		group["type"] = "fallback"
		delete(group, "strategy")
	}
	if c.plan.TestURL != "" {
		group["url"] = c.plan.TestURL
	}
	return outbounds, group
}
