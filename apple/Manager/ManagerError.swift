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
    message: "The Solaris network extension is ready after the Mac restarts")
}
