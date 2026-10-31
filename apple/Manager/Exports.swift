import Foundation
import SolarisManagerHeader

// The C functions declared in SolarisManager.h. Each converts between C and Swift and leaves the
// work to SystemExtensionRequests, TunnelController and CloudBackup.

@_cdecl("solaris_manager_activate")
public func solarisManagerActivate(
  _ timeout: Double, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try SystemExtensionRequests.shared.activate(timeout: timeout) }
}

@_cdecl("solaris_manager_deactivate")
public func solarisManagerDeactivate(
  _ timeout: Double, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try TunnelController.shared.deactivate(timeout: timeout) }
}

@_cdecl("solaris_manager_configure")
public func solarisManagerConfigure(
  _ includeAllNetworks: Bool, _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) { try TunnelController.shared.configure(includeAllNetworks: includeAllNetworks) }
}

@_cdecl("solaris_manager_start")
public func solarisManagerStart(
  _ timeout: Double, _ includeAllNetworks: Bool,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) {
    try TunnelController.shared.start(timeout: timeout, includeAllNetworks: includeAllNetworks)
  }
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
    response.pointee = try copy(reply)
    responseLength.pointee = reply.count
  }
}

@_cdecl("solaris_manager_icloud_status")
public func solarisManagerICloudStatus(
  _ timeout: Double, _ status: UnsafeMutablePointer<Int32>?,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) {
    guard let status else { throw ManagerError.failed("status must not be NULL") }
    status.pointee = SolarisICloudStatus(try CloudBackup.shared.status(timeout: timeout))
  }
}

@_cdecl("solaris_manager_icloud_save")
public func solarisManagerICloudSave(
  _ name: UnsafePointer<CChar>?, _ info: UnsafePointer<CChar>?,
  _ snapshot: UnsafePointer<UInt8>?, _ snapshotLength: Int, _ timeout: Double,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) {
    guard let name, let info, snapshot != nil || snapshotLength == 0 else {
      throw ManagerError.failed("name, info and snapshot must not be NULL")
    }
    let data = snapshot.map { Data(bytes: $0, count: snapshotLength) } ?? Data()
    try CloudBackup.shared.save(
      name: String(cString: name), info: String(cString: info), snapshot: data, timeout: timeout)
  }
}

@_cdecl("solaris_manager_icloud_list")
public func solarisManagerICloudList(
  _ timeout: Double, _ list: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  list?.pointee = nil
  return run(error) {
    guard let list else { throw ManagerError.failed("list must not be NULL") }
    let json = try CloudBackup.shared.list(timeout: timeout)
    guard let copied = strdup(String(decoding: json, as: UTF8.self)) else {
      throw ManagerError.failed("Out of memory for the list")
    }
    list.pointee = copied
  }
}

@_cdecl("solaris_manager_icloud_download")
public func solarisManagerICloudDownload(
  _ name: UnsafePointer<CChar>?, _ timeout: Double,
  _ snapshot: UnsafeMutablePointer<UnsafeMutablePointer<UInt8>?>?,
  _ snapshotLength: UnsafeMutablePointer<Int>?,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  snapshot?.pointee = nil
  snapshotLength?.pointee = 0
  return run(error) {
    guard let name, let snapshot, let snapshotLength else {
      throw ManagerError.failed("name, snapshot and snapshot_length must not be NULL")
    }
    let data = try CloudBackup.shared.download(name: String(cString: name), timeout: timeout)
    snapshot.pointee = try copy(data)
    snapshotLength.pointee = data.count
  }
}

@_cdecl("solaris_manager_icloud_delete")
public func solarisManagerICloudDelete(
  _ name: UnsafePointer<CChar>?, _ timeout: Double,
  _ error: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
) -> Int32 {
  run(error) {
    guard let name else { throw ManagerError.failed("name must not be NULL") }
    try CloudBackup.shared.delete(name: String(cString: name), timeout: timeout)
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

/// `data` in a buffer the caller releases with solaris_manager_free. malloc(0) may return NULL,
/// and an empty Response is still a Response, so the buffer has at least one byte.
private func copy(_ data: Data) throws -> UnsafeMutablePointer<UInt8> {
  guard let buffer = malloc(max(data.count, 1))?.assumingMemoryBound(to: UInt8.self) else {
    throw ManagerError.failed("Out of memory for \(data.count) bytes")
  }
  data.copyBytes(to: buffer, count: data.count)
  return buffer
}

private func same<T>(_: T, _: T) {}

// Never called: it compiles only while each export matches its declaration in SolarisManager.h.
private func checkHeader() {
  same(solaris_manager_activate, solarisManagerActivate)
  same(solaris_manager_deactivate, solarisManagerDeactivate)
  same(solaris_manager_configure, solarisManagerConfigure)
  same(solaris_manager_start, solarisManagerStart)
  same(solaris_manager_stop, solarisManagerStop)
  same(solaris_manager_status, solarisManagerStatus)
  same(solaris_manager_send, solarisManagerSend)
  same(solaris_manager_icloud_status, solarisManagerICloudStatus)
  same(solaris_manager_icloud_save, solarisManagerICloudSave)
  same(solaris_manager_icloud_list, solarisManagerICloudList)
  same(solaris_manager_icloud_download, solarisManagerICloudDownload)
  same(solaris_manager_icloud_delete, solarisManagerICloudDelete)
  same(solaris_manager_free, solarisManagerFree)
}
