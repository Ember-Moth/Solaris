import Foundation
import NetworkExtension
import os

/// Sets up the tunnel, runs the Go core on its utun and hands control messages to the Go
/// dispatcher (docs/architecture.md §6.2).
final class PacketTunnelProvider: NEPacketTunnelProvider {
  /// Messages are dispatched off the provider's callbacks. The dispatcher serializes the methods
  /// that change state and lets queries run in parallel, so the queue is concurrent.
  private let messages = DispatchQueue(
    label: "dev.tschen.solaris.tunnel.messages", attributes: .concurrent)
  /// Attaching in startTunnel and detaching in stopTunnel run here, in that order.
  private let lifecycle = DispatchQueue(label: "dev.tschen.solaris.tunnel.lifecycle")
  /// Set by stopTunnel, so that a start still waiting for its network settings does not
  /// attach afterwards. Read and written on `lifecycle` only.
  private var stopping = false

  override init() {
    super.init()
    do { Core.crashLogs(home: try Self.coreHome()) }
    catch { logger.error("crash directory unavailable: \(error.localizedDescription, privacy: .public)") }
  }

  override func startTunnel(
    options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void
  ) {
    // The engine's configuration arrives later through Start and Reload messages, not in
    // `options` (docs/decisions.md D7).
    let network: TunnelNetwork
    do {
      network = try Core.network()
    } catch {
      completionHandler(error)
      return
    }
    setTunnelNetworkSettings(network.settings()) { error in
      self.lifecycle.async {
        completionHandler(self.attach(after: error))
      }
    }
  }

  /// Hands the utun to the Go core once NetworkExtension has set up the network.
  private func attach(after settingsError: Error?) -> Error? {
    if let settingsError {
      logger.error("network settings failed: \(settingsError.localizedDescription, privacy: .public)")
      return settingsError
    }
    guard !stopping else { return TunnelError("The tunnel stopped while it was starting") }
    // NetworkExtension opens the utun only now, and NEPacketTunnelProvider does not expose
    // its descriptor.
    guard let tunnel = TunnelDescriptor.find() else {
      return TunnelError("Could not find the tunnel's utun descriptor")
    }
    // GeoSite.dat and GeoIP.dat, which the build copies into the extension's resources.
    guard let resources = Bundle.main.resourceURL else {
      return TunnelError("The extension has no resource directory")
    }
    // The VPN configuration's setting (D19): with it, the core cannot run the system or mixed
    // stack and runs gVisor instead.
    let includeAllNetworks = protocolConfiguration.includeAllNetworks
    do {
      try Core.attach(
        tunnel.fileDescriptor, home: try Self.coreHome(), resources: resources,
        includeAllNetworks: includeAllNetworks)
    } catch {
      logger.error("attaching the core failed: \(error.localizedDescription, privacy: .public)")
      return error
    }
    logger.info(
      "core attached to \(tunnel.interfaceName, privacy: .public), fd \(tunnel.fileDescriptor), includeAllNetworks \(includeAllNetworks)"
    )
    return nil
  }

  override func stopTunnel(
    with reason: NEProviderStopReason, completionHandler: @escaping () -> Void
  ) {
    logger.info("stopping, reason \(reason.rawValue)")
    lifecycle.async {
      self.stopping = true
      // Go stops the core and closes its duplicates of the descriptor before
      // NetworkExtension, after the completion handler, closes the descriptor itself.
      Core.detach()
      completionHandler()
    }
  }

  override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
    messages.async {
      completionHandler?(Core.handle(messageData))
    }
  }

  /// mihomo's home, in the extension's container; Go creates it.
  private static func coreHome() throws -> URL {
    try FileManager.default.url(
      for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
    ).appendingPathComponent("Solaris/core", isDirectory: true)
  }
}

struct TunnelError: LocalizedError {
  let errorDescription: String?

  init(_ description: String) { errorDescription = description }
}

let logger = Logger(subsystem: "dev.tschen.solaris", category: "tunnel")
