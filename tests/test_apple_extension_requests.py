"""System extension delegate transitions with an injected submitter; no requests reach macOS."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MANAGER = Path(__file__).resolve().parents[1] / "apple" / "Manager"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None, reason="needs Swift on macOS"
)
def test_native_extension_request_lifecycle_without_submitting_to_the_system(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main.swift"
    main.write_text(r"""
import Foundation
import SystemExtensions
import SolarisManagerHeader
import os

let tunnelBundleIdentifier = "dev.tschen.solaris.tunnel"
let logger = Logger(subsystem: "dev.tschen.solaris.tests", category: "fake-extensions")

final class Submitter {
  var mode = "approval"
  var requests: [OSSystemExtensionRequest] = []
  func submit(_ request: OSSystemExtensionRequest) {
    precondition(request.identifier == tunnelBundleIdentifier)
    requests.append(request)
    switch mode {
    case "approval": request.delegate!.requestNeedsUserApproval(request)
    case "complete": request.delegate!.request(request, didFinishWithResult: .completed)
    case "pending":
      request.delegate!.request(request, didFinishWithResult: .willCompleteAfterReboot)
    case "absent":
      request.delegate!.request(request, didFailWithError: NSError(
        domain: OSSystemExtensionErrorDomain,
        code: OSSystemExtensionError.Code.extensionNotFound.rawValue))
    case "cancelled":
      request.delegate!.request(request, didFailWithError: NSError(
        domain: OSSystemExtensionErrorDomain,
        code: OSSystemExtensionError.Code.requestCanceled.rawValue))
    case "late":
      Thread.sleep(forTimeInterval: 0.03)
      request.delegate!.request(request, didFinishWithResult: .completed)
    default: break
    }
  }
}

func expect(_ result: Int, _ action: () throws -> Void) {
  do { try action(); fatalError("unexpected success") }
  catch let error as ManagerError { precondition(error.result == result) }
  catch { fatalError("unexpected error") }
}

let fake = Submitter()
let controller = SystemExtensionRequests(submit: fake.submit)
expect(SOLARIS_MANAGER_NEEDS_APPROVAL) { try controller.activate(timeout: 0.2) }
expect(SOLARIS_MANAGER_NEEDS_APPROVAL) { try controller.activate(timeout: 0.2) }
expect(SOLARIS_MANAGER_BUSY) { try controller.deactivate(timeout: 0.2) }
precondition(fake.requests.count == 1)
let first = fake.requests[0]
first.delegate!.request(first, didFinishWithResult: .completed)

fake.mode = "absent"
try controller.deactivate(timeout: 0.2)
fake.mode = "pending"
expect(SOLARIS_MANAGER_NEEDS_REBOOT) { try controller.deactivate(timeout: 0.2) }
fake.mode = "complete"
try controller.deactivate(timeout: 0.2) // pending completion was not cached as an eternal refusal
fake.mode = "cancelled"
expect(SOLARIS_MANAGER_CANCELLED) { try controller.deactivate(timeout: 0.2) }
fake.mode = "complete"
try controller.activate(timeout: 0.2)

fake.mode = "silent"
expect(SOLARIS_MANAGER_TIMED_OUT) { try controller.deactivate(timeout: 0.02) }
let count = fake.requests.count
expect(SOLARIS_MANAGER_TIMED_OUT) { try controller.deactivate(timeout: 0.02) }
expect(SOLARIS_MANAGER_BUSY) { try controller.activate(timeout: 0.2) }
precondition(fake.requests.count == count)
let pending = fake.requests.last!
pending.delegate!.request(pending, didFinishWithResult: .completed)

fake.mode = "late"
expect(SOLARIS_MANAGER_TIMED_OUT) { try controller.deactivate(timeout: 0.01) }
fake.mode = "complete"
try controller.deactivate(timeout: 0.2)
print("extension request checks passed")
""")
    executable = tmp_path / "extension-requests"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-I",
            str(MANAGER),
            str(MANAGER / "Deadline.swift"),
            str(MANAGER / "ManagerError.swift"),
            str(MANAGER / "SystemExtensionRequests.swift"),
            str(main),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = subprocess.run(
        [str(executable)], check=True, capture_output=True, text=True, timeout=5
    )
    assert result.stdout.strip() == "extension request checks passed"
