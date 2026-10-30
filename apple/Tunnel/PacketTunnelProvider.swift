import Foundation
import NetworkExtension
import os

/// Sets up the tunnel and hands control messages to the Go dispatcher (docs/architecture.md §6.2).
final class PacketTunnelProvider: NEPacketTunnelProvider {
  /// Messages are dispatched off the provider's callbacks. The dispatcher serializes the methods
  /// that change state and lets queries run in parallel, so the queue is concurrent.
  private let messages = DispatchQueue(
    label: "dev.tschen.solaris.tunnel.messages", attributes: .concurrent)

  override func startTunnel(
    options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void
  ) {
    // The engine's configuration arrives later through Start and Reload messages, not in
    // `options` (docs/roadmap.md D7).
    setTunnelNetworkSettings(TunnelAddresses.settings()) { error in
      if let error {
        logger.error("network settings failed: \(error.localizedDescription, privacy: .public)")
        completionHandler(error)
        return
      }
      guard let tunnel = TunnelDescriptor.find() else {
        completionHandler(TunnelError("Could not find the tunnel's utun descriptor"))
        return
      }
      logger.info("tunnel on \(tunnel.interfaceName, privacy: .public), fd \(tunnel.fileDescriptor)")
      // TODO(M5): hand tunnel.fileDescriptor to the engine's fd mode (engine.TunFD) through a new
      // export from src/core/apple, so that the dispatcher runs Start and Reload on it. Until Go
      // has it, nothing reads the tunnel, so refuse to connect rather than drop all traffic.
      completionHandler(TunnelError("The engine's fd mode is not implemented yet"))
    }
  }

  override func stopTunnel(
    with reason: NEProviderStopReason, completionHandler: @escaping () -> Void
  ) {
    logger.info("stopping, reason \(reason.rawValue)")
    // TODO(M5): stop the engine once startTunnel starts it.
    completionHandler()
  }

  override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
    messages.async {
      completionHandler?(ControlDispatcher.handle(messageData))
    }
  }
}

struct TunnelError: LocalizedError {
  let errorDescription: String?

  init(_ description: String) { errorDescription = description }
}

let logger = Logger(subsystem: "dev.tschen.solaris", category: "tunnel")
