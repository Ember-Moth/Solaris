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
  private let lifecycle = NSLock()
  private let cacheLock = NSLock()
  private var cached: NETunnelProviderManager?

  func configure() throws {
    try lifecycle.withLock { _ = try configured() }
  }

  func start(timeout: TimeInterval) throws {
    let deadline = Date(timeIntervalSinceNow: timeout)
    try lifecycle.withLock {
      let connection = try configured().connection
      if connection.status == .disconnecting {
        try waitUntilDown(connection, StatusChanges(connection), before: deadline)
      }
      if connection.status == .connected { return }
      let changes = StatusChanges(connection)
      if [.disconnected, .invalid].contains(connection.status) {
        logger.info("starting the tunnel")
        // No options: the engine's configuration arrives later as a Start message (D7).
        try connection.startVPNTunnel()
      }
      while connection.status != .connected {
        // Right after the request the status may still read disconnected; once it has moved,
        // falling back to disconnected means the extension gave up.
        if changes.seen && [.disconnected, .invalid].contains(connection.status) {
          throw ManagerError.failed(try disconnectReason(connection))
        }
        guard Date() < deadline else {
          throw ManagerError.timedOut("The Solaris tunnel did not connect in time")
        }
        changes.next(before: min(deadline, Date(timeIntervalSinceNow: 0.25)))
      }
    }
  }

  func stop(timeout: TimeInterval) throws {
    let deadline = Date(timeIntervalSinceNow: timeout)
    try lifecycle.withLock {
      guard let connection = try current()?.connection else { return }
      let changes = StatusChanges(connection)
      if ![.disconnected, .disconnecting, .invalid].contains(connection.status) {
        logger.info("stopping the tunnel")
        connection.stopVPNTunnel()
      }
      try waitUntilDown(connection, changes, before: deadline)
    }
  }

  /// A SOLARIS_TUNNEL_ value from SolarisManager.h.
  func status() throws -> Int {
    guard let manager = try current() else { return SOLARIS_TUNNEL_NOT_CONFIGURED }
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
    guard let manager = try current() else { throw ManagerError.notConfigured }
    guard [.connected, .reasserting].contains(manager.connection.status),
      let session = manager.connection as? NETunnelProviderSession
    else { throw ManagerError.notConnected }
    let reply: Data? = try waitForCompletion("reach the extension", timeout: timeout) { done in
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
  /// took over) or points elsewhere. Callers hold `lifecycle`.
  private func configured() throws -> NETunnelProviderManager {
    let manager = try load() ?? NETunnelProviderManager()
    if manager.isEnabled, manager.localizedDescription == configurationName,
      (manager.protocolConfiguration as? NETunnelProviderProtocol)?.providerBundleIdentifier
        == tunnelBundleIdentifier
    {
      return manager
    }
    let tunnelProtocol = NETunnelProviderProtocol()
    tunnelProtocol.providerBundleIdentifier = tunnelBundleIdentifier
    // Shown as the server in System Settings; the extension does not read it.
    tunnelProtocol.serverAddress = configurationName
    manager.protocolConfiguration = tunnelProtocol
    manager.localizedDescription = configurationName
    manager.isEnabled = true
    logger.info("saving the VPN configuration")
    try waitForCompletion("save the VPN configuration") { done in
      manager.saveToPreferences { done($0.map { .failure($0) } ?? .success(())) }
    }
    // A configuration starts reliably only after it has been loaded again since saving.
    try waitForCompletion("reload the VPN configuration") { done in
      manager.loadFromPreferences { done($0.map { .failure($0) } ?? .success(())) }
    }
    cacheLock.withLock { cached = manager }
    return manager
  }

  /// The cached configuration while it is valid, otherwise a fresh load.
  private func current() throws -> NETunnelProviderManager? {
    if let manager = cacheLock.withLock({ cached }), manager.connection.status != .invalid {
      return manager
    }
    return try load()
  }

  private func load() throws -> NETunnelProviderManager? {
    let managers: [NETunnelProviderManager] = try waitForCompletion(
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
    _ connection: NEVPNConnection, _ changes: StatusChanges, before deadline: Date
  ) throws {
    while ![.disconnected, .invalid].contains(connection.status) {
      guard Date() < deadline else {
        throw ManagerError.timedOut("The Solaris tunnel did not stop in time")
      }
      changes.next(before: min(deadline, Date(timeIntervalSinceNow: 0.25)))
    }
  }

  private func disconnectReason(_ connection: NEVPNConnection) throws -> String {
    let error: Error? = try waitForCompletion("read why the tunnel stopped", timeout: 2) { done in
      connection.fetchLastDisconnectError { done(.success($0)) }
    }
    guard let error else { return "The Solaris tunnel stopped while connecting" }
    return "The Solaris tunnel stopped while connecting: \(error.localizedDescription)"
  }
}

/// Waits for an operation that reports through a completion handler.
private func waitForCompletion<T>(
  _ action: String, timeout: TimeInterval = preferencesTimeout,
  _ operation: (@escaping (Result<T, Error>) -> Void) -> Void
) throws -> T {
  let done = DispatchSemaphore(value: 0)
  let lock = NSLock()
  var result: Result<T, Error>?
  operation { value in
    lock.withLock { result = value }
    done.signal()
  }
  guard done.wait(timeout: .now() + timeout) == .success,
    let value = lock.withLock({ result })
  else { throw ManagerError.timedOut("Timed out trying to \(action)") }
  return try value.get()
}

/// Counts a connection's status notifications, so that waiting loops wake up on changes.
private final class StatusChanges {
  private let condition = NSCondition()
  private var count = 0
  private var observer: NSObjectProtocol?

  init(_ connection: NEVPNConnection) {
    observer = NotificationCenter.default.addObserver(
      forName: .NEVPNStatusDidChange, object: connection, queue: nil
    ) { [weak self] _ in
      guard let self else { return }
      self.condition.withLock {
        self.count += 1
        self.condition.broadcast()
      }
    }
  }

  deinit {
    if let observer { NotificationCenter.default.removeObserver(observer) }
  }

  var seen: Bool { condition.withLock { count > 0 } }

  /// Returns after the next change, or at `limit`.
  func next(before limit: Date) {
    condition.withLock {
      let start = count
      while count == start, condition.wait(until: limit) {}
    }
  }
}
