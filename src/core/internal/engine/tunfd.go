package engine

import "net/netip"

// TunnelNetwork is the TUN's side of the tunnel in fd mode.
type TunnelNetwork struct {
	Inet4 netip.Prefix
	Inet6 netip.Prefix
	MTU   uint32
}

// DNSServer is the resolver the host gives the system: the address after the TUN's own,
// which mihomo always hijacks (sing_tun.New adds Inet4Address.Addr().Next():53).
func (n TunnelNetwork) DNSServer() netip.Addr {
	return n.Inet4.Addr().Next()
}

// fdFakeIPRange is mihomo's default fake-ip range. mihomo gives its TUN the range's first
// address as a /30, so the fd patch pins the range to keep the TUN on FDNetwork.Inet4.
const fdFakeIPRange = "198.18.0.1/16"

// FDNetwork is the network of the tunnel in fd mode: mihomo's TUN defaults. The macOS
// extension gives its utun these addresses and makes DNSServer the system's resolver
// (src/core/apple exports them); the fd patch gives mihomo's TUN the same ones.
var FDNetwork = TunnelNetwork{
	Inet4: netip.PrefixFrom(netip.MustParsePrefix(fdFakeIPRange).Addr(), 30),
	Inet6: netip.MustParsePrefix("fdfe:dcba:9876::1/126"),
	MTU:   9000,
}
