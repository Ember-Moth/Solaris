import Foundation
import SolarisManagerHeader

// The C functions declared in SolarisManager.h. Each converts between C and Swift and leaves the
// work to ExtensionActivation and TunnelController.

@_cdecl("solaris_manager_activate")
public func solarisManagerActivate(
  _ timeout: Double, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try ExtensionActivation.shared.activate(timeout: timeout) }
}

@_cdecl("solaris_manager_configure")
public func solarisManagerConfigure(
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try TunnelController.shared.configure() }
}

@_cdecl("solaris_manager_start")
public func solarisManagerStart(
  _ timeout: Double, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try TunnelController.shared.start(timeout: timeout) }
}

@_cdecl("solaris_manager_stop")
public func solarisManagerStop(
  _ timeout: Double, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try TunnelController.shared.stop(timeout: timeout) }
}

@_cdecl("solaris_manager_status")
public func solarisManagerStatus(
  _ status: UnsafeMutablePointer<Int32>?,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) {
    guard let status else { throw ManagerError.failed("status must not be NULL") }
    status.pointee = SolarisTunnelStatus(try TunnelController.shared.status())
  }
}

@_cdecl("solaris_manager_send")
public func solarisManagerSend(
  _ request: UnsafePointer<UInt8>?, _ requestLength: Int, _ timeout: Double,
  _ response: UnsafeMutablePointer<UnsafeMutablePointer<UInt8>?>?,
  _ responseLength: UnsafeMutablePointer<Int>?,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  response?.pointee = nil
  responseLength?.pointee = 0
  return run(error) {
    guard let response, let responseLength, request != nil || requestLength == 0 else {
      throw ManagerError.failed("request, response and response_length must not be NULL")
    }
    let message = request.map { Data(bytes: $0, count: requestLength) } ?? Data()
    let reply = try TunnelController.shared.send(message, timeout: timeout)
    // malloc(0) may return NULL, and an empty Response is still a Response.
    guard let buffer = malloc(max(reply.count, 1))?.assumingMemoryBound(to: UInt8.self) else {
      throw ManagerError.failed("Out of memory for the extension's response")
    }
    reply.copyBytes(to: buffer, count: reply.count)
    response.pointee = buffer
    responseLength.pointee = reply.count
  }
}

@_cdecl("solaris_manager_free")
public func solarisManagerFree(_ pointer: UnsafeMutableRawPointer?) {
  free(pointer)
}

/// Runs one call: a thrown ManagerError gives the result code and the description for `error`.
private func run(
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?, _ body: () throws -> Void
) -> Int32 {
  error?.pointee = nil
  let failure: ManagerError
  do {
    guard !Thread.isMainThread else {
      throw ManagerError.failed("SolarisManager blocks; call it from a background thread")
    }
    try body()
    return SolarisManagerResult(SOLARIS_MANAGER_OK)
  } catch let managerError as ManagerError {
    failure = managerError
  } catch let other {
    failure = ManagerError.failed(other.localizedDescription)
  }
  error?.pointee = strdup(failure.message)
  return SolarisManagerResult(failure.result)
}

private func same<T>(_: T, _: T) {}

// Never called: it compiles only while each export matches its declaration in SolarisManager.h.
private func checkHeader() {
  same(solaris_manager_activate, solarisManagerActivate)
  same(solaris_manager_configure, solarisManagerConfigure)
  same(solaris_manager_start, solarisManagerStart)
  same(solaris_manager_stop, solarisManagerStop)
  same(solaris_manager_status, solarisManagerStatus)
  same(solaris_manager_send, solarisManagerSend)
  same(solaris_manager_free, solarisManagerFree)
}
