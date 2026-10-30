import NetworkExtension

/// The tunnel's network as the Go core defines it (engine.FDNetwork, read with
/// Core.network()). The utun gets the addresses mihomo's TUN uses, and the system's DNS
/// goes to the address after the TUN's own, which mihomo always hijacks.
struct TunnelNetwork: Decodable {
  let ipv4Address: String
  let ipv4SubnetMask: String
  let ipv6Address: String
  let ipv6PrefixLength: Int
  let dnsServer: String
  let mtu: Int

  /// All IPv4 and IPv6 traffic and all DNS go through the tunnel.
  func settings() -> NEPacketTunnelNetworkSettings {
    // NetworkExtension keeps the remote address out of the tunnel's routes; there is no
    // remote server, so loopback keeps that exception harmless.
    let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
    let ipv4Settings = NEIPv4Settings(addresses: [ipv4Address], subnetMasks: [ipv4SubnetMask])
    ipv4Settings.includedRoutes = [NEIPv4Route.default()]
    settings.ipv4Settings = ipv4Settings
    let ipv6Settings = NEIPv6Settings(
      addresses: [ipv6Address], networkPrefixLengths: [NSNumber(value: ipv6PrefixLength)])
    ipv6Settings.includedRoutes = [NEIPv6Route.default()]
    settings.ipv6Settings = ipv6Settings
    let dnsSettings = NEDNSSettings(servers: [dnsServer])
    dnsSettings.matchDomains = [""]
    settings.dnsSettings = dnsSettings
    settings.mtu = NSNumber(value: mtu)
    return settings
  }
}
