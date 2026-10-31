from __future__ import annotations

import os
import sys

import pytest

from solaris import platforms
from solaris.update.releases import installer_name


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("AMD64", "x64"), ("x86_64", "x64"), ("ARM64", "arm64"), ("aarch64", "arm64"), ("x86", "x86")],
)
def test_machine_aliases(machine: str, expected: str) -> None:
    assert platforms.normalize_machine(machine) == expected


def test_emulated_x64_uses_arm64_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platforms.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(platforms, "_windows_native_machine", lambda: "arm64")
    assert platforms.native_machine("win32") == "arm64"
    assert installer_name("1.0.0", "win32") == "Solaris-1.0.0-windows-arm64-setup.exe"
    # Explicit overrides, used by callers/tests, still select the requested package.
    assert installer_name("1.0.0", "win32", "AMD64") == "Solaris-1.0.0-windows-x64-setup.exe"


def test_machine_falls_back_when_native_query_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platforms, "_windows_native_machine", lambda: None)
    monkeypatch.setattr(platforms.platform, "machine", lambda: "aarch64")
    assert platforms.native_machine("win32") == "arm64"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows architecture API")
def test_windows_native_machine_api() -> None:
    machine = platforms._windows_native_machine()
    assert machine in {"x64", "arm64"}
    if runner := os.environ.get("RUNNER_ARCH"):
        assert machine == platforms.normalize_machine(runner)
