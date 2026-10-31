import SolarisManagerHeader

/// Why a call failed: `result` is its SOLARIS_MANAGER_ code from SolarisManager.h.
struct ManagerError: Error {
  let result: Int
  let message: String

  static func failed(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_FAILED, message: message)
  }

  static func timedOut(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_TIMED_OUT, message: message)
  }

  static let notConfigured = ManagerError(
    result: SOLARIS_MANAGER_NOT_CONFIGURED, message: "Solaris has no VPN configuration yet")

  static let notConnected = ManagerError(
    result: SOLARIS_MANAGER_NOT_CONNECTED, message: "The Solaris tunnel is not connected")

  static let needsApproval = ManagerError(
    result: SOLARIS_MANAGER_NEEDS_APPROVAL,
    message: "Allow the Solaris network extension in System Settings")

  static let needsReboot = ManagerError(
    result: SOLARIS_MANAGER_NEEDS_REBOOT,
    message: "macOS has not completed the system extension request")

  static let busy = ManagerError(
    result: SOLARIS_MANAGER_BUSY, message: "Another system extension request is still pending")

  static let cancelled = ManagerError(
    result: SOLARIS_MANAGER_CANCELLED, message: "The system extension request was cancelled")

  static func notSignedIn(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_NOT_SIGNED_IN, message: message)
  }

  static func notFound(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_NOT_FOUND, message: message)
  }

  static func quotaExceeded(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_QUOTA_EXCEEDED, message: message)
  }

  static func unreachable(_ message: String) -> ManagerError {
    ManagerError(result: SOLARIS_MANAGER_UNREACHABLE, message: message)
  }
}
