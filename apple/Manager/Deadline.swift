import Foundation

/// One monotonic budget for a native operation, including its queue and every callback.
/// Reusing it across steps prevents each preferences call from restarting the timeout.
struct Deadline {
  let end: DispatchTime

  init(seconds: TimeInterval) throws {
    guard seconds.isFinite, seconds > 0, seconds <= 600 else {
      throw ManagerError.failed("The operation timeout must be between 0 and 600 seconds")
    }
    end = .now() + seconds
  }

  func check(_ action: String) throws {
    guard DispatchTime.now() < end else {
      throw ManagerError.timedOut("Timed out trying to \(action)")
    }
  }

  func remaining(_ action: String) throws -> TimeInterval {
    let now = DispatchTime.now().uptimeNanoseconds
    guard now < end.uptimeNanoseconds else { throw ManagerError.timedOut("Timed out trying to \(action)") }
    return TimeInterval(end.uptimeNanoseconds - now) / 1_000_000_000
  }

  func withLock<T>(_ gate: DispatchSemaphore, _ action: String, _ work: () throws -> T) throws -> T {
    try check(action)
    guard gate.wait(timeout: end) == .success else {
      throw ManagerError.timedOut("Timed out waiting to \(action)")
    }
    defer { gate.signal() }
    try check(action)
    return try work()
  }

  func wait<T>(
    _ action: String, _ operation: (@escaping (Result<T, Error>) -> Void) -> Void
  ) throws -> T {
    try check(action)
    let completion = Completion<T>()
    operation { completion.finish($0) }
    guard completion.done.wait(timeout: end) == .success else {
      throw ManagerError.timedOut("Timed out trying to \(action)")
    }
    try check(action)
    return try completion.value().get()
  }
}

private final class Completion<T>: @unchecked Sendable {
  let done = DispatchSemaphore(value: 0)
  private let lock = NSLock()
  private var result: Result<T, Error>?

  func finish(_ value: Result<T, Error>) {
    lock.withLock {
      if result == nil {
        result = value
        done.signal()
      }
    }
  }

  func value() -> Result<T, Error> { lock.withLock { result! } }
}
