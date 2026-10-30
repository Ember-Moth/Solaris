import CloudKit
import Foundation
import Security
import SolarisManagerHeader

private let containerIdentifier = "iCloud.dev.tschen.solaris"
/// The record type of a snapshot, with the string field `info` and the asset field `snapshot`.
/// The container's production schema has to define it (apple/README.md).
private let recordType = "Snapshot"
private let infoKey = "info"
private let snapshotKey = "snapshot"
/// How much longer than its own timeout an operation gets to report before the call gives up.
private let reportGrace: TimeInterval = 5

/// Snapshots in the CloudKit private database (docs/storage.md §5): the user's own iCloud
/// storage, which only this app reads. The Python provider names and describes each snapshot;
/// this class stores them as they are.
final class CloudBackup {
  static let shared = CloudBackup()

  /// Nil in a build without the iCloud entitlements: CloudKit raises an Objective-C exception
  /// there, which would end the process, so the container is never created.
  private let container: CKContainer?

  private init() {
    container = hasContainerEntitlement() ? CKContainer(identifier: containerIdentifier) : nil
  }

  /// A SOLARIS_ICLOUD_ value from SolarisManager.h.
  func status(timeout: TimeInterval) throws -> Int {
    guard let container else { return SOLARIS_ICLOUD_UNSUPPORTED }
    let action = "read the iCloud account status"
    let status: CKAccountStatus
    do {
      status = try waitForResult(action, timeout: timeout) { done in
        container.accountStatus { status, error in
          done(error.map { .failure($0) } ?? .success(status))
        }
      }
    } catch let error as ManagerError {
      throw error
    } catch {
      throw managerError(error, action)
    }
    switch status {
    case .available: return SOLARIS_ICLOUD_AVAILABLE
    case .noAccount: return SOLARIS_ICLOUD_NO_ACCOUNT
    case .restricted: return SOLARIS_ICLOUD_RESTRICTED
    case .temporarilyUnavailable: return SOLARIS_ICLOUD_TEMPORARILY_UNAVAILABLE
    case .couldNotDetermine: return SOLARIS_ICLOUD_COULD_NOT_DETERMINE
    @unknown default: return SOLARIS_ICLOUD_COULD_NOT_DETERMINE
    }
  }

  func save(name: String, info: String, snapshot: Data, timeout: TimeInterval) throws {
    let database = try privateDatabase()
    let record = CKRecord(recordType: recordType, recordID: try recordID(name))
    // An asset uploads from a file, which has to stay until the save is over.
    let file = FileManager.default.temporaryDirectory.appendingPathComponent(
      "solaris-\(UUID().uuidString).json")
    do {
      try snapshot.write(to: file)
    } catch {
      throw ManagerError.failed("Could not stage the snapshot: \(error.localizedDescription)")
    }
    defer { try? FileManager.default.removeItem(at: file) }
    record[infoKey] = info as NSString
    record[snapshotKey] = CKAsset(fileURL: file)
    let operation = CKModifyRecordsOperation(recordsToSave: [record], recordIDsToDelete: nil)
    // A name saved again replaces the record, whatever its server copy holds.
    operation.savePolicy = .allKeys
    try perform("save the snapshot to iCloud", operation, on: database, timeout: timeout) {
      (done: @escaping (Result<Void, Error>) -> Void) in
      var failure: Error?
      operation.perRecordSaveBlock = { _, result in
        if case .failure(let error) = result { failure = error }
      }
      operation.modifyRecordsResultBlock = { result in
        done(failure.map { .failure($0) } ?? result)
      }
    }
  }

  /// The saved snapshots as a JSON array of {"name", "info"}.
  func list(timeout: TimeInterval) throws -> Data {
    let database = try privateDatabase()
    let deadline = Date(timeIntervalSinceNow: timeout)
    var entries: [[String: String]] = []
    var cursor: CKQueryOperation.Cursor?
    repeat {
      let operation =
        cursor.map { CKQueryOperation(cursor: $0) }
        ?? CKQueryOperation(
          query: CKQuery(recordType: recordType, predicate: NSPredicate(value: true)))
      operation.desiredKeys = [infoKey]
      var page: [[String: String]] = []
      do {
        cursor = try perform(
          "list the snapshots in iCloud", operation, on: database,
          timeout: max(deadline.timeIntervalSinceNow, 1)
        ) { done in
          operation.recordMatchedBlock = { id, result in
            if case .success(let record) = result, let info = record[infoKey] as? String {
              page.append(["name": id.recordName, "info": info])
            }
          }
          operation.queryResultBlock = { done($0) }
        }
      } catch let error as ManagerError where error.result == SOLARIS_MANAGER_NOT_FOUND {
        // A query on a record type the schema lacks reports an unknown item.
        throw ManagerError.failed(
          "The iCloud container has no \(recordType) record type; deploy its schema")
      }
      entries += page
    } while cursor != nil
    do {
      return try JSONSerialization.data(withJSONObject: entries)
    } catch {
      throw ManagerError.failed("Could not encode the list: \(error.localizedDescription)")
    }
  }

  func download(name: String, timeout: TimeInterval) throws -> Data {
    let database = try privateDatabase()
    let operation = CKFetchRecordsOperation(recordIDs: [try recordID(name)])
    operation.desiredKeys = [snapshotKey]
    return try perform("download the snapshot from iCloud", operation, on: database, timeout: timeout) {
      done in
      var found: Result<Data, Error>?
      operation.perRecordResultBlock = { _, result in
        // The asset's file lasts only as long as the operation: read it now.
        found = result.flatMap { record in
          guard let url = (record[snapshotKey] as? CKAsset)?.fileURL else {
            return .failure(ManagerError.failed("The snapshot \(name) in iCloud has no content"))
          }
          return Result { try Data(contentsOf: url) }
        }
      }
      operation.fetchRecordsResultBlock = { result in
        done(
          found
            ?? result.flatMap { .failure(ManagerError.notFound("No snapshot \(name) in iCloud")) })
      }
    }
  }

  func delete(name: String, timeout: TimeInterval) throws {
    let database = try privateDatabase()
    let operation = CKModifyRecordsOperation(
      recordsToSave: nil, recordIDsToDelete: [try recordID(name)])
    do {
      try perform("delete the snapshot from iCloud", operation, on: database, timeout: timeout) {
        (done: @escaping (Result<Void, Error>) -> Void) in
        var failure: Error?
        operation.perRecordDeleteBlock = { _, result in
          if case .failure(let error) = result { failure = error }
        }
        operation.modifyRecordsResultBlock = { result in
          done(failure.map { .failure($0) } ?? result)
        }
      }
    } catch let error as ManagerError where error.result == SOLARIS_MANAGER_NOT_FOUND {
      return  // gone already
    }
  }

  private func privateDatabase() throws -> CKDatabase {
    guard let container else {
      throw ManagerError.failed("This build of Solaris is not signed for iCloud")
    }
    return container.privateCloudDatabase
  }

  private func recordID(_ name: String) throws -> CKRecord.ID {
    guard !name.isEmpty, name.utf8.count <= 255, name.allSatisfy(\.isASCII) else {
      throw ManagerError.failed("A snapshot's name must be 1 to 255 ASCII characters")
    }
    return CKRecord.ID(recordName: name)
  }

  /// Runs `operation` and waits until the blocks that `setUp` gives it report; the operation is
  /// cancelled when they do not in time.
  private func perform<T>(
    _ action: String, _ operation: CKDatabaseOperation, on database: CKDatabase,
    timeout: TimeInterval, _ setUp: (@escaping (Result<T, Error>) -> Void) -> Void
  ) throws -> T {
    // userInitiated: a lower quality of service lets the system put the transfer off.
    operation.configuration.qualityOfService = .userInitiated
    operation.configuration.timeoutIntervalForRequest = timeout
    operation.configuration.timeoutIntervalForResource = timeout
    do {
      return try waitForResult(action, timeout: timeout + reportGrace, cancel: operation.cancel) {
        done in
        setUp(done)
        database.add(operation)
      }
    } catch let error as ManagerError {
      throw error
    } catch {
      throw managerError(error, action)
    }
  }
}

/// Waits for an operation that reports through a completion handler, calling `cancel` when it
/// does not in time.
private func waitForResult<T>(
  _ action: String, timeout: TimeInterval, cancel: (() -> Void)? = nil,
  _ start: (@escaping (Result<T, Error>) -> Void) -> Void
) throws -> T {
  let done = DispatchSemaphore(value: 0)
  let lock = NSLock()
  var result: Result<T, Error>?
  start { value in
    lock.withLock { result = result ?? value }
    done.signal()
  }
  guard done.wait(timeout: .now() + timeout) == .success,
    let value = lock.withLock({ result })
  else {
    cancel?()
    throw ManagerError.timedOut("Timed out trying to \(action)")
  }
  return try value.get()
}

/// The ManagerError of a CloudKit failure.
private func managerError(_ error: Error, _ action: String) -> ManagerError {
  let message = "Could not \(action): \(error.localizedDescription)"
  guard let error = error as? CKError else { return .failed(message) }
  switch error.code {
  case .partialFailure:
    guard let first = error.partialErrorsByItemID?.values.first else { return .failed(message) }
    return managerError(first, action)
  case .notAuthenticated, .accountTemporarilyUnavailable:
    return .notSignedIn(message)
  case .unknownItem:
    return .notFound(message)
  case .quotaExceeded:
    return .quotaExceeded(message)
  case .networkUnavailable, .networkFailure, .serviceUnavailable, .requestRateLimited, .zoneBusy:
    return .unreachable(message)
  default:
    return .failed(message)
  }
}

/// Whether the process is signed for the container.
private func hasContainerEntitlement() -> Bool {
  guard let task = SecTaskCreateFromSelf(nil),
    let value = SecTaskCopyValueForEntitlement(
      task, "com.apple.developer.icloud-container-identifiers" as CFString, nil),
    let containers = value as? [String]
  else { return false }
  return containers.contains(containerIdentifier)
}
