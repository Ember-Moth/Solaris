import Darwin

/// The utun socket NetworkExtension opened for this provider. NEPacketTunnelProvider does not
/// expose it, so look through the open descriptors for a kernel control socket on a utun interface.
struct TunnelDescriptor {
  let fileDescriptor: Int32
  let interfaceName: String

  static func find() -> TunnelDescriptor? {
    for descriptor: Int32 in 0...1024 {
      if let name = interfaceName(descriptor) {
        return TunnelDescriptor(fileDescriptor: descriptor, interfaceName: name)
      }
    }
    return nil
  }

  private static func interfaceName(_ descriptor: Int32) -> String? {
    // SYSPROTO_CONTROL from <sys/sys_domain.h>, UTUN_OPT_IFNAME from <net/if_utun.h>.
    let controlLevel: Int32 = 2
    let interfaceNameOption: Int32 = 2
    var buffer = [CChar](repeating: 0, count: Int(IFNAMSIZ))
    var length = socklen_t(buffer.count)
    let name = buffer.withUnsafeMutableBufferPointer { pointer -> String? in
      guard getsockopt(descriptor, controlLevel, interfaceNameOption, pointer.baseAddress, &length) == 0
      else { return nil }
      return String(cString: pointer.baseAddress!)
    }
    guard let name, name.hasPrefix("utun") else { return nil }
    return name
  }
}
