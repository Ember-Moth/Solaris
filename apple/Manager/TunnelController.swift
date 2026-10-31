import Foundation
import NetworkExtension
import os
import SolarisManagerHeader

let tunnelBundleIdentifier = "dev.tschen.solaris.tunnel"
let logger = Logger(subsystem: "dev.tschen.solaris", category: "manager")

/// The configuration's name in System Settings > VPN.
private let configurationName = "Solaris"
private let preferencesTimeout: TimeInterval = 20

/// The app's VPN configuration for the extension (NETunnelProviderManager) and its connection.
final class TunnelController {
  static let shared = TunnelController()

  /// configure, start and stop run one at a time.
  private let lifecycle = DispatchSemaphore(value: 1)
  private let cacheLock = NSLock()
  private var cached: NETunnelProviderManager?

  func configure(includeAllNetworks: Bool) throws {
    let deadline = try Deadline(seconds: preferencesTimeout)
    try deadline.withLock(lifecycle, "configure the tunnel") {
      _ = try configured(includeAllNetworks: includeAllNetworks, before: deadline)
    }
  }

  func start(timeout: TimeInterval, includeAllNetworks: Bool) throws {
    let deadline = try Deadline(seconds: timeout)
    try deadline.withLock(lifecycle, "start the tunnel") {
      var connection = try configured(includeAllNetworks: includeAllNetworks, before: deadline).connection
      if connection.status == .disconnecting {
        try waitUntilDown(connection, StatusChanges(connection), before: deadline)
        // Down now, so the configuration takes the setting before the tunnel connects again.
        connection = try configured(includeAllNetworks: includeAllNetworks, before: deadline).connection
      }
      if connection.status == .connected { return }
      let changes = StatusChanges(connection)
      if [.disconnected, .invalid].contains(connection.status) {
        try deadline.check("start the tunnel")
        logger.info("starting the tunnel")
        // No options: the engine's configuration arrives later as a Start message (D7).
        try connection.startVPNTunnel()
      }
      while connection.status != .connected {
        // Right after the request the status may still read disconnected; once it has moved,
        // falling back to disconnected means the extension gave up.
        if changes.seen && [.disconnected, .invalid].contains(connection.status) {
          throw ManagerError.failed(try disconnectReason(connection, before: deadline))
        }
        try deadline.check("connect the tunnel")
        changes.next(before: min(deadline.end, .now() + 0.25))
      }
    }
  }

  func stop(timeout: TimeInterval) throws {
    let deadline = try Deadline(seconds: timeout)
    try deadline.withLock(lifecycle, "stop the tunnel") {
      guard let connection = try current(before: deadline)?.connection else { return }
      let changes = StatusChanges(connection)
      if ![.disconnected, .disconnecting, .invalid].contains(connection.status) {
        try deadline.check("stop the tunnel")
        logger.info("stopping the tunnel")
        connection.stopVPNTunnel()
      }
      try waitUntilDown(connection, changes, before: deadline)
    }
  }

  /// A SOLARIS_TUNNEL_ value from SolarisManager.h.
  func status() throws -> Int {
    let deadline = try Deadline(seconds: preferencesTimeout)
    guard let manager = try current(before: deadline) else { return SOLARIS_TUNNEL_NOT_CONFIGURED }
    switch manager.connection.status {
    case .invalid: return SOLARIS_TUNNEL_INVALID
    case .disconnected: return SOLARIS_TUNNEL_DISCONNECTED
    case .connecting: return SOLARIS_TUNNEL_CONNECTING
    case .connected: return SOLARIS_TUNNEL_CONNECTED
    case .reasserting: return SOLARIS_TUNNEL_REASSERTING
    case .disconnecting: return SOLARIS_TUNNEL_DISCONNECTING
    @unknown default: return SOLARIS_TUNNEL_INVALID
    }
  }

  /// Exchanges one provider message; the extension answers in handleAppMessage.
  func send(_ message: Data, timeout: TimeInterval) throws -> Data {
    let deadline = try Deadline(seconds: timeout)
    guard let manager = try current(before: deadline) else { throw ManagerError.notConfigured }
    guard [.connected, .reasserting].contains(manager.connection.status),
      let session = manager.connection as? NETunnelProviderSession
    else { throw ManagerError.notConnected }
    let reply: Data? = try deadline.wait("reach the extension") { done in
      do {
        try session.sendProviderMessage(message) { done(.success($0)) }
      } catch {
        done(.failure(error))
      }
    }
    guard let reply else { throw ManagerError.failed("The Solaris extension did not answer") }
    return reply
  }

  /// The configuration, created or repaired and saved when it is missing, disabled (another VPN
  /// took over) or points elsewhere, or when it includes all networks or not other than asked
  /// (D19). A tunnel that is up keeps the setting it connected with: the extension reads it once,
  /// as it attaches the core, so a change takes effect on the next connection. Callers hold
  /// `lifecycle`.
  private func configured(includeAllNetworks: Bool, before deadline: Deadline) throws -> NETunnelProviderManager {
    let manager = try load(before: deadline) ?? NETunnelProviderManager()
    let current = manager.protocolConfiguration as? NETunnelProviderProtocol
    let down = [.disconnected, .invalid].contains(manager.connection.status)
    if manager.isEnabled, manager.localizedDescription == configurationName,
      current?.providerBundleIdentifier == tunnelBundleIdentifier,
      current?.includeAllNetworks == includeAllNetworks || !down
    {
      return manager
    }
    let tunnelProtocol = NETunnelProviderProtocol()
    tunnelProtocol.providerBundleIdentifier = tunnelBundleIdentifier
    // Shown as the server in System Settings; the extension does not read it.
    tunnelProtocol.serverAddress = configurationName
    // Traffic that bypasses the tunnel by default enters it too, except the local network's:
    // printers, AirDrop and Bonjour keep working.
    tunnelProtocol.includeAllNetworks = includeAllNetworks
    tunnelProtocol.excludeLocalNetworks = includeAllNetworks
    manager.protocolConfiguration = tunnelProtocol
    manager.localizedDescription = configurationName
    manager.isEnabled = true
    logger.info("saving the VPN configuration, includeAllNetworks \(includeAllNetworks)")
    try deadline.wait("save the VPN configuration") { done in
      manager.saveToPreferences { done($0.map { .failure($0) } ?? .success(())) }
    }
    // A configuration starts reliably only after it has been loaded again since saving.
    try deadline.wait("reload the VPN configuration") { done in
      manager.loadFromPreferences { done($0.map { .failure($0) } ?? .success(())) }
    }
    cacheLock.withLock { cached = manager }
    return manager
  }

  /// The cached configuration while it is valid, otherwise a fresh load.
  private func current(before deadline: Deadline) throws -> NETunnelProviderManager? {
    try deadline.check("read the VPN configuration")
    if let manager = cacheLock.withLock({ cached }), manager.connection.status != .invalid {
      return manager
    }
    return try load(before: deadline)
  }

  private func load(before deadline: Deadline) throws -> NETunnelProviderManager? {
    let managers: [NETunnelProviderManager] = try deadline.wait(
      "load the VPN configuration"
    ) { done in
      NETunnelProviderManager.loadAllFromPreferences { managers, error in
        done(error.map { .failure($0) } ?? .success(managers ?? []))
      }
    }
    let manager = managers.first {
      ($0.protocolConfiguration as? NETunnelProviderProtocol)?.providerBundleIdentifier
        == tunnelBundleIdentifier
    }
    cacheLock.withLock { cached = manager }
    return manager
  }

  private func waitUntilDown(
    _ connection: NEVPNConnection, _ changes: StatusChanges, before deadline: Deadline
  ) throws {
    while ![.disconnected, .invalid].contains(connection.status) {
      try deadline.check("stop the tunnel")
      changes.next(before: min(deadline.end, .now() + 0.25))
    }
  }

  private func disconnectReason(_ connection: NEVPNConnection, before deadline: Deadline) throws -> String {
    let error: Error? = try deadline.wait("read why the tunnel stopped") { done in
      connection.fetchLastDisconnectError { done(.success($0)) }
    }
    guard let error else { return "The Solaris tunnel stopped while connecting" }
    return "The Solaris tunnel stopped while connecting: \(error.localizedDescription)"
  }
}

/// Counts a connection's status notifications, so that waiting loops wake up on changes.
private final class StatusChanges {
  private let lock = NSLock()
  private let changed = DispatchSemaphore(value: 0)
  private var count = 0
  private var observer: NSObjectProtocol?

  init(_ connection: NEVPNConnection) {
    observer = NotificationCenter.default.addObserver(
      forName: .NEVPNStatusDidChange, object: connection, queue: nil
    ) { [weak self] _ in
      guard let self else { return }
      self.lock.withLock {
        self.count += 1
        self.changed.signal()
      }
    }
  }

  deinit {
    if let observer { NotificationCenter.default.removeObserver(observer) }
  }

  var seen: Bool { lock.withLock { count > 0 } }

  /// Returns after the next change, or at `limit`.
  func next(before limit: DispatchTime) {
    _ = changed.wait(timeout: limit)
  }
}
