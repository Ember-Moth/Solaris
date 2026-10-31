import Foundation
import os
import SystemExtensions

/// Owns the pending system request across UI retries. Activation and deactivation may
/// not overlap; a timed-out or approval-pending request remains owned until its callback.
final class SystemExtensionRequests: NSObject, OSSystemExtensionRequestDelegate {
  static let shared = SystemExtensionRequests()

  enum Action: String { case activate, deactivate }
  private let queue = DispatchQueue(label: "dev.tschen.solaris.manager.extensions")
  private let lock = NSLock()
  private let submit: (OSSystemExtensionRequest) -> Void
  private var pending: Attempt?

  /// Tests inject a submitter which only calls the delegate; they never submit to macOS.
  init(submit: @escaping (OSSystemExtensionRequest) -> Void = {
    OSSystemExtensionManager.shared.submitRequest($0)
  }) {
    self.submit = submit
    super.init()
  }

  func activate(timeout: TimeInterval) throws { try perform(.activate, timeout: timeout) }
  func deactivate(timeout: TimeInterval) throws { try perform(.deactivate, timeout: timeout) }

  private func perform(_ action: Action, timeout: TimeInterval) throws {
    let deadline = try Deadline(seconds: timeout)
    let (attempt, created): (Attempt, Bool) = try lock.withLock {
      if let pending {
        guard pending.action == action else { throw ManagerError.busy }
        return (pending, false)
      }
      let request = action == .activate
        ? OSSystemExtensionRequest.activationRequest(forExtensionWithIdentifier: tunnelBundleIdentifier, queue: queue)
        : OSSystemExtensionRequest.deactivationRequest(forExtensionWithIdentifier: tunnelBundleIdentifier, queue: queue)
      request.delegate = self
      let attempt = Attempt(request, action: action)
      pending = attempt
      return (attempt, true)
    }
    if created {
      do { try deadline.check("submit the system extension request") }
      catch { finish(attempt.request, .failure(error)); throw error }
      logger.info("submitting extension request: \(action.rawValue, privacy: .public)")
      submit(attempt.request)
    }
    while true {
      try deadline.check("\(action.rawValue) the system extension")
      let (outcome, needsApproval) = lock.withLock { (attempt.outcome, attempt.needsApproval) }
      if let outcome {
        switch try outcome.get() {
        case .completed: return
        case .willCompleteAfterReboot: throw ManagerError.needsReboot
        @unknown default: throw ManagerError.failed("The system extension request returned an unknown result")
        }
      }
      if needsApproval { throw ManagerError.needsApproval }
      _ = attempt.changed.wait(timeout: min(deadline.end, .now() + 0.25))
    }
  }

  func request(_ request: OSSystemExtensionRequest, didFinishWithResult result: OSSystemExtensionRequest.Result) {
    finish(request, .success(result))
  }

  func request(_ request: OSSystemExtensionRequest, didFailWithError error: Error) {
    let failure = error as NSError
    let action = lock.withLock { pending?.request === request ? pending?.action : nil }
    if failure.domain == OSSystemExtensionErrorDomain {
      switch failure.code {
      case OSSystemExtensionError.Code.extensionNotFound.rawValue where action == .deactivate:
        finish(request, .success(.completed)) // already absent; its VPN preferences may still need cleanup
        return
      case OSSystemExtensionError.Code.requestCanceled.rawValue:
        finish(request, .failure(ManagerError.cancelled))
        return
      case OSSystemExtensionError.Code.requestSuperseded.rawValue:
        finish(request, .failure(ManagerError.busy))
        return
      case OSSystemExtensionError.Code.authorizationRequired.rawValue:
        finish(request, .failure(ManagerError.needsApproval))
        return
      default: break
      }
    }
    logger.error("extension request failed: \(failure.localizedDescription, privacy: .public)")
    finish(request, .failure(ManagerError.failed(failure.localizedDescription)))
  }

  func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
    lock.withLock {
      guard let pending, pending.request === request else { return }
      pending.needsApproval = true
      pending.changed.signal()
    }
  }

  func request(
    _ request: OSSystemExtensionRequest, actionForReplacingExtension existing: OSSystemExtensionProperties,
    withExtension replacement: OSSystemExtensionProperties
  ) -> OSSystemExtensionRequest.ReplacementAction {
    // UI, manager and extension are distributed as one protocol-compatible bundle.
    .replace
  }

  private func finish(_ request: OSSystemExtensionRequest, _ outcome: Result<OSSystemExtensionRequest.Result, Error>) {
    lock.withLock {
      guard let pending, pending.request === request else { return }
      pending.outcome = outcome
      self.pending = nil
      pending.changed.signal()
    }
  }
}

private final class Attempt {
  let request: OSSystemExtensionRequest
  let action: SystemExtensionRequests.Action
  let changed = DispatchSemaphore(value: 0)
  var outcome: Result<OSSystemExtensionRequest.Result, Error>?
  var needsApproval = false

  init(_ request: OSSystemExtensionRequest, action: SystemExtensionRequests.Action) {
    self.request = request
    self.action = action
  }
}
