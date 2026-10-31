"""Exercise the native deadline without creating a VPN configuration or extension request."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1] / "apple" / "Manager"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None, reason="needs Swift on macOS"
)
def test_native_deadline_includes_queue_and_all_callback_steps(tmp_path: Path) -> None:
    main = tmp_path / "main.swift"
    main.write_text(r"""
import Foundation
import SolarisManagerHeader

func timedOut(_ work: () throws -> Void) {
  do {
    try work()
    fatalError("operation unexpectedly completed")
  } catch let error as ManagerError {
    precondition(error.result == SOLARIS_MANAGER_TIMED_OUT)
  } catch { fatalError("unexpected error: \(error)") }
}

let blocked = DispatchSemaphore(value: 0)
var entered = false
timedOut {
  try Deadline(seconds: 0.02).withLock(blocked, "enter") { entered = true }
}
precondition(!entered)
blocked.signal()
try Deadline(seconds: 1).withLock(blocked, "enter again") { entered = true }
precondition(entered)

let chain = try Deadline(seconds: 0.08)
let first: Int = try chain.wait("first") { done in
  DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) { done(.success(1)) }
}
precondition(first == 1)
timedOut {
  let _: Int = try chain.wait("second") { done in
    DispatchQueue.global().asyncAfter(deadline: .now() + 0.08) { done(.success(2)) }
  }
}

var submitted = false
timedOut {
  let _: Int = try chain.wait("expired") { done in
    submitted = true
    done(.success(3))
  }
}
precondition(!submitted)
let next: Int = try Deadline(seconds: 1).wait("next request") { done in done(.success(4)) }
precondition(next == 4)
Thread.sleep(forTimeInterval: 0.08) // the old callback can finish without changing the next result
print("deadline checks passed")
""")
    binary = tmp_path / "deadline-test"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-I",
            str(NATIVE),
            str(NATIVE / "Deadline.swift"),
            str(NATIVE / "ManagerError.swift"),
            str(main),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == "deadline checks passed"
