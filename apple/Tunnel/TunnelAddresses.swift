import NetworkExtension

/// The tunnel's addresses: mihomo's TUN defaults (198.18.0.1/30 from the fake-IP range,
/// fdfe:dcba:9876::1/126, MTU 9000), which the engine's fd mode must use as well.
enum TunnelAddresses {
  static let ipv4 = "198.18.0.1"
  static let ipv4Mask = "255.255.255.252"
  static let ipv6 = "fdfe:dcba:9876::1"
  static let ipv6PrefixLength = 126
  /// The other address on the /30. Queries to it enter the tunnel, where mihomo's dns-hijack
  /// (0.0.0.0:53 by default) answers them.
  static let dnsServer = "198.18.0.2"
  static let mtu = 9000

  /// All IPv4 and IPv6 traffic and all DNS go through the tunnel.
  static func settings() -> NEPacketTunnelNetworkSettings {
    // NetworkExtension keeps the remote address out of the tunnel's routes; there is no remote
    // server, so loopback keeps that exception harmless.
    let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
    let ipv4Settings = NEIPv4Settings(addresses: [ipv4], subnetMasks: [ipv4Mask])
    ipv4Settings.includedRoutes = [NEIPv4Route.default()]
    settings.ipv4Settings = ipv4Settings
    let ipv6Settings = NEIPv6Settings(
      addresses: [ipv6], networkPrefixLengths: [NSNumber(value: ipv6PrefixLength)])
    ipv6Settings.includedRoutes = [NEIPv6Route.default()]
    settings.ipv6Settings = ipv6Settings
    let dnsSettings = NEDNSSettings(servers: [dnsServer])
    dnsSettings.matchDomains = [""]
    settings.dnsSettings = dnsSettings
    settings.mtu = NSNumber(value: mtu)
    return settings
  }
}
