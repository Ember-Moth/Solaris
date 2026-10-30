import Foundation
import SolarisCore

/// The Go control dispatcher linked from libsolaris.a (src/core/apple). Swift only forwards
/// bytes; the dispatcher decodes the Request, checks its size and encodes the Response.
enum ControlDispatcher {
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
