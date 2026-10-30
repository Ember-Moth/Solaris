import Foundation
import SolarisCore

/// The Go core linked from libsolaris.a (src/core/apple): the engine in fd mode and the
/// control dispatcher. Swift only forwards bytes; Go decodes the Request, checks its size and
/// encodes the Response.
enum Core {
  /// The tunnel's network as Go defines it (engine.FDNetwork).
  static func network() throws -> TunnelNetwork {
    var length = 0
    guard let buffer = solaris_tunnel_network(&length) else {
      throw TunnelError("The core did not describe the tunnel's network")
    }
    defer { solaris_free(buffer) }
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(TunnelNetwork.self, from: Data(bytes: buffer, count: length))
  }

  /// Opens the engine in fd mode on the utun descriptor. Go runs mihomo on duplicates of
  /// it and never closes the descriptor itself.
  static func attach(_ fileDescriptor: Int32, home: URL) throws {
    let failure = home.path.withCString {
      solaris_attach_tun(fileDescriptor, UnsafeMutablePointer(mutating: $0))
    }
    if let failure {
      defer { solaris_free(failure) }
      throw TunnelError(String(cString: failure))
    }
  }

  /// Stops the core and closes Go's duplicates of the descriptor. It waits for the messages
  /// in flight, which it cancels; afterwards NetworkExtension may close the descriptor.
  static func detach() {
    solaris_detach_tun()
  }

  static func handle(_ request: Data) -> Data? {
    request.withUnsafeBytes { bytes in
      var length = 0
      guard
        let response = solaris_handle_message(
          UnsafeMutableRawPointer(mutating: bytes.baseAddress), bytes.count, &length)
      else { return nil }
      defer { solaris_free(response) }
      return Data(bytes: response, count: length)
    }
  }
}
