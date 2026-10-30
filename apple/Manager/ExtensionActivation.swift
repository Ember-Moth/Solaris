import Foundation
import os
import SystemExtensions

/// Activates the system extension bundled in the app's Contents/Library/SystemExtensions.
final class ExtensionActivation: NSObject, OSSystemExtensionRequestDelegate {
  static let shared = ExtensionActivation()

  private let queue = DispatchQueue(label: "dev.tschen.solaris.manager.activation")
  private let condition = NSCondition()
  /// The submitted request, kept until it finishes so that later calls wait for it instead of
  /// submitting another.
  private var pending: Attempt?

  func activate(timeout: TimeInterval) throws {
    let deadline = Date(timeIntervalSinceNow: timeout)
    condition.lock()
    defer { condition.unlock() }
    let attempt: Attempt
    if let pending {
      attempt = pending
    } else {
      let request = OSSystemExtensionRequest.activationRequest(
        forExtensionWithIdentifier: tunnelBundleIdentifier, queue: queue)
      request.delegate = self
      attempt = Attempt(request)
      pending = attempt
      logger.info("submitting the activation request")
      OSSystemExtensionManager.shared.submitRequest(request)
    }
    while true {
      if let outcome = attempt.outcome {
        switch try outcome.get() {
        case .completed:
          return
        case .willCompleteAfterReboot:
          throw ManagerError.needsReboot
        @unknown default:
          throw ManagerError.failed("System extension activation ended with an unknown result")
        }
      }
      if attempt.needsApproval && !attempt.approvalReported {
        attempt.approvalReported = true
        throw ManagerError.needsApproval
      }
      if !condition.wait(until: deadline) {
        throw attempt.needsApproval
          ? ManagerError.needsApproval
          : ManagerError.timedOut("System extension activation did not finish in time")
      }
    }
  }

  func request(
    _ request: OSSystemExtensionRequest,
    didFinishWithResult result: OSSystemExtensionRequest.Result
  ) {
    logger.info("activation finished: \(result.rawValue)")
    finish(request, .success(result))
  }

  func request(_ request: OSSystemExtensionRequest, didFailWithError error: Error) {
    logger.error("activation failed: \(error.localizedDescription, privacy: .public)")
    finish(
      request,
      .failure(ManagerError.failed("System extension activation failed: \(error.localizedDescription)")))
  }

  func requestNeedsUserApproval(_ request: OSSystemExtensionRequest) {
    logger.info("activation needs the user's approval")
    condition.withLock {
      guard let pending, pending.request === request else { return }
      pending.needsApproval = true
      condition.broadcast()
    }
  }

  func request(
    _ request: OSSystemExtensionRequest,
    actionForReplacingExtension existing: OSSystemExtensionProperties,
    withExtension replacement: OSSystemExtensionProperties
  ) -> OSSystemExtensionRequest.ReplacementAction {
    logger.info(
      "replacing \(existing.bundleShortVersion, privacy: .public) (\(existing.bundleVersion, privacy: .public)) with \(replacement.bundleShortVersion, privacy: .public) (\(replacement.bundleVersion, privacy: .public))"
    )
    // The bundled extension is the one that speaks this app's control protocol, so it wins over
    // any other build, older or newer.
    return .replace
  }

  private func finish(
    _ request: OSSystemExtensionRequest, _ outcome: Result<OSSystemExtensionRequest.Result, Error>
  ) {
    condition.withLock {
      guard let pending, pending.request === request else { return }
      pending.outcome = outcome
      self.pending = nil
      condition.broadcast()
    }
  }
}

private final class Attempt {
  let request: OSSystemExtensionRequest
  var outcome: Result<OSSystemExtensionRequest.Result, Error>?
  var needsApproval = false
  /// Whether a call has returned SOLARIS_MANAGER_NEEDS_APPROVAL early; later calls wait instead.
  var approvalReported = false

  init(_ request: OSSystemExtensionRequest) { self.request = request }
}
