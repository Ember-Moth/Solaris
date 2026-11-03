"""Apple packaging and isolated Swift probes; never activate an extension or create a VPN."""

from __future__ import annotations

import json
import plistlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_app, build_apple, build_dmg

MANAGER = build_apple.APPLE / "Manager"
NATIVE_MACOS = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None,
    reason="needs Swift and macOS bundle APIs",
)


def _compile_swift(source: Path, executable: Path, manager_sources: tuple[str, ...] = ()) -> None:
    command = ["xcrun", "swiftc"]
    if manager_sources:
        command += ["-I", str(MANAGER), *(str(MANAGER / name) for name in manager_sources)]
    command += [str(source), "-o", str(executable)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize(
    ("library_minimum", "expected"), [("15.0", "26.0"), ("26.0", "26.0"), ("27.0", "27.0")]
)
def test_app_minimum_keeps_product_floor_and_stricter_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, library_minimum: str, expected: str
) -> None:
    bundle = tmp_path / "Solaris.app"
    binary = bundle / "Contents" / "MacOS" / "SolarisUI"
    monkeypatch.setattr(build_app, "_machos", lambda _: iter([binary]))

    def output(command: list[str | Path]) -> str:
        if command[0] == "lipo":
            return "arm64"
        assert command[0] == "otool"
        return f"Load command 0\n cmd LC_BUILD_VERSION\n minos {library_minimum}\n"

    monkeypatch.setattr(build_app, "_output", output)
    assert build_app.check_ui(bundle) == expected


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk, which follows the 8-byte signature."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[12:16] == b"IHDR"
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def test_the_background_fills_the_window_at_both_scales() -> None:
    """dmgbuild pairs background.png with background@2x.png; tiffutil wants exactly twice."""
    width, height = build_dmg.WINDOW_SIZE
    double = build_dmg.BACKGROUND.with_stem(f"{build_dmg.BACKGROUND.stem}@2x")
    assert _png_size(build_dmg.BACKGROUND) == (width, height)
    assert _png_size(double) == (2 * width, 2 * height)


def test_the_app_sits_left_of_applications_inside_the_window() -> None:
    settings = build_dmg.settings(Path("build/app/Solaris.app"))
    (app_x, app_y), (applications_x, applications_y) = (
        settings["icon_locations"][name] for name in (build_dmg.APP_NAME, build_dmg.APPLICATIONS)
    )
    width, height = build_dmg.WINDOW_SIZE
    half = build_dmg.ICON_SIZE / 2
    assert app_y == applications_y
    assert half <= app_x < applications_x <= width - half
    assert half <= app_y <= height - half
    assert settings["symlinks"] == {build_dmg.APPLICATIONS: "/Applications"}
    assert Path(settings["icon"]).is_file()
    assert Path(settings["background"]).is_file()
    # SetFile's Finder information on the bundle would break its signature.
    assert "hide_extensions" not in settings


@NATIVE_MACOS
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

let chain = try Deadline(seconds: 5)
let first: Int = try chain.wait("first") { done in done(.success(1)) }
precondition(first == 1)
// Consume the original budget explicitly. No successful callback depends on a
// global queue being scheduled inside a few milliseconds on a busy CI runner.
_ = DispatchSemaphore(value: 0).wait(timeout: chain.end)

var submitted = false
timedOut {
  let _: Int = try chain.wait("expired") { done in
    submitted = true
    done(.success(3))
  }
}
precondition(!submitted)
var late: ((Result<Int, Error>) -> Void)?
timedOut {
  let _: Int = try Deadline(seconds: 0.02).wait("pending callback") { done in late = done }
}
precondition(late != nil)
let next: Int = try Deadline(seconds: 5).wait("next request") { done in
  late?(.success(2)) // deliver the old result while the new request is pending
  done(.success(4))
}
precondition(next == 4)
print("deadline checks passed")
""")
    binary = tmp_path / "deadline-test"
    _compile_swift(main, binary, ("Deadline.swift", "ManagerError.swift"))
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "deadline checks passed"


@NATIVE_MACOS
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
    _compile_swift(
        main, executable, ("Deadline.swift", "ManagerError.swift", "SystemExtensionRequests.swift")
    )
    result = subprocess.run(
        [str(executable)], check=True, capture_output=True, text=True, timeout=5
    )
    assert result.stdout.strip() == "extension request checks passed"


@pytest.fixture
def extension(tmp_path: Path) -> Path:
    bundle = tmp_path / "Solaris.systemextension"
    contents = bundle / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    info = plistlib.loads((build_apple.APPLE / "Tunnel" / "Info.plist").read_bytes())
    info.update(CFBundleDevelopmentRegion="en", CFBundleIdentifier=build_apple.TUNNEL_ID)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    for language in build_apple.LOCALIZATIONS:
        relative = f"{language}.lproj"
        shutil.copytree(build_apple.APPLE / "Tunnel" / relative, resources / relative)
    return bundle


@NATIVE_MACOS
def test_bundle_resolves_chinese_english_and_fallback(extension: Path, tmp_path: Path) -> None:
    source = tmp_path / "main.swift"
    source.write_text("""
import Foundation
let bundle = Bundle(path: CommandLine.arguments[1])!
let value = bundle.object(forInfoDictionaryKey: "NSSystemExtensionUsageDescription") as! String
print(String(data: try! JSONSerialization.data(withJSONObject: [value]), encoding: .utf8)!)
""")
    # Foundation restricts dependent bundle languages to the main app's localizations.
    # Match the app assembled by build_app, rather than an unlocalized command-line tool.
    app = tmp_path / "Lookup.app" / "Contents"
    executable = app / "MacOS" / "lookup"
    executable.parent.mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "lookup",
                "CFBundleIdentifier": "dev.tschen.solaris.localization-test",
                "CFBundleDevelopmentRegion": "en",
                "CFBundleLocalizations": list(build_apple.LOCALIZATIONS),
            }
        )
    )
    _compile_swift(source, executable)
    build_apple.check_localizations(extension)
    english = plistlib.loads((extension / "Contents" / "Info.plist").read_bytes())[
        "NSSystemExtensionUsageDescription"
    ]
    for language in ("en", "zh-Hans", "zh-CN", "fr"):
        result = subprocess.run(
            [str(executable), str(extension), "-AppleLanguages", f"({language})"],
            check=True,
            capture_output=True,
            text=True,
        )
        [message] = json.loads(result.stdout)
        if language.startswith("zh"):
            assert "按照你的连接设置处理网络流量" in message
        else:
            assert message == english


@NATIVE_MACOS
def test_packaging_rejects_missing_or_stale_localization(extension: Path) -> None:
    path = extension / "Contents/Resources/zh-Hans.lproj/InfoPlist.strings"
    path.write_text('"NSSystemExtensionUsageDescription" = "old";')
    with pytest.raises(build_apple.BuildError, match="differs"):
        build_apple.check_localizations(extension)
    path.unlink()
    with pytest.raises(build_apple.BuildError, match="missing"):
        build_apple.check_localizations(extension)
