"""scripts/build_windows.py: the install directory's checks, the uninstall list and the NSIS
compile. The real build runs on Windows in CI; here the installer is compiled around a stand-in
directory, when makensis is installed."""

from __future__ import annotations

import ctypes
import re
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_installer_ui, build_windows
from scripts.build_apple import BuildError


def _pe(machine: int) -> bytes:
    """A minimal PE header: the DOS stub's pointer at 0x3C, then the signature and machine."""
    return b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0" + struct.pack("<H", machine)


def _install_directory(root: Path, machine: int = build_windows.PE_MACHINE_AMD64) -> Path:
    directory = root / "Solaris"
    for name in build_windows.REQUIRED:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_pe(machine) if name.endswith((".exe", ".dll")) else b"x")
    qml = directory / "PySide6" / "qml" / "QtQuick"
    qml.mkdir(parents=True)
    (qml / "qtquick2plugin.dll").write_bytes(_pe(machine))
    (qml / "price$1.qml").write_text("Item {}")
    return directory


def test_pe_machine(tmp_path: Path) -> None:
    path = tmp_path / "a.dll"
    path.write_bytes(_pe(0x8664))
    assert build_windows.pe_machine(path) == 0x8664
    path.write_bytes(_pe(0x14C))
    assert build_windows.pe_machine(path) == 0x14C
    path.write_bytes(b"MZ" + bytes(10))
    assert build_windows.pe_machine(path) is None
    path.write_bytes(b"not a program")
    assert build_windows.pe_machine(path) is None


def test_check_counts_the_x64_binaries(tmp_path: Path) -> None:
    assert build_windows.check(_install_directory(tmp_path)) == 4


def test_arm64_package_rejects_mixed_binaries(tmp_path: Path) -> None:
    directory = _install_directory(tmp_path, build_windows.PE_MACHINE_ARM64)
    assert build_windows.check(directory, "arm64") == 4
    (directory / "libcronet.dll").write_bytes(_pe(build_windows.PE_MACHINE_AMD64))
    with pytest.raises(BuildError, match=r"libcronet\.dll.*not arm64"):
        build_windows.check(directory, "arm64")


@pytest.mark.parametrize("arch", ["x64", "arm64"])
def test_python_architecture_comes_from_the_interpreter_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arch: str
) -> None:
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(_pe(build_windows.target(arch)[1]))
    monkeypatch.setattr(sys, "executable", str(interpreter))
    assert build_windows.python_architecture() == arch


def test_gui_build_refuses_an_emulated_python_for_the_native_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_windows, "python_architecture", lambda: "x64")
    with pytest.raises(BuildError, match="native arm64 Python"):
        build_windows.build_ui("1.0.0", "arm64")


@pytest.mark.parametrize("arch", ["x64", "arm64"])
@pytest.mark.parametrize("name", ["GeoSite.dat", "GeoIP.dat", "geoip.metadb", "ASN.mmdb"])
def test_check_wants_every_geodata_file(tmp_path: Path, arch: str, name: str) -> None:
    directory = _install_directory(tmp_path, build_windows.target(arch)[1])
    (directory / "geodata" / name).unlink()
    with pytest.raises(BuildError, match="has no geodata/"):
        build_windows.check(directory, arch)


def test_check_refuses_other_architectures(tmp_path: Path) -> None:
    directory = _install_directory(tmp_path)
    (directory / "PySide6" / "x86.pyd").write_bytes(_pe(0x14C))
    with pytest.raises(BuildError, match=r"PySide6/x86\.pyd is machine 0x014c"):
        build_windows.check(directory)
    (directory / "PySide6" / "x86.pyd").write_bytes(b"text")
    with pytest.raises(BuildError, match="not a PE file"):
        build_windows.check(directory)


def test_uninstall_list_removes_contents_before_their_directories(tmp_path: Path) -> None:
    lines = build_windows.uninstall_list(_install_directory(tmp_path)).splitlines()
    assert '!insertmacro RemoveInstalledFile "$INSTDIR\\Solaris.exe"' in lines
    assert (
        '!insertmacro RemoveInstalledFile "$INSTDIR\\PySide6\\qml\\QtQuick\\price$$1.qml"' in lines
    )
    assert all(line.startswith(("!insertmacro RemoveInstalledFile ", "RMDir ")) for line in lines)
    assert not any("/r" in line for line in lines)
    order = {line: index for index, line in enumerate(lines)}
    for directory in ("PySide6", "PySide6\\qml", "PySide6\\qml\\QtQuick", "geodata"):
        removed = order[f'RMDir "$INSTDIR\\{directory}"']
        inside = [i for line, i in order.items() if f"$INSTDIR\\{directory}\\" in line]
        assert inside
        assert max(inside) < removed


def test_nsis_string() -> None:
    assert build_windows.nsis_string('C:\\a "$b"') == 'C:\\a $\\"$$b$\\"'
    with pytest.raises(BuildError):
        build_windows.nsis_string("a\nb")


def test_installer_name() -> None:
    # docs/release.md §1, which the updater looks for.
    assert build_windows.installer_name("0.2.0") == "Solaris-0.2.0-windows-x64-setup.exe"
    assert build_windows.installer_name("0.2.0", "arm64") == "Solaris-0.2.0-windows-arm64-setup.exe"
    with pytest.raises(BuildError, match="unsupported Windows architecture"):
        build_windows.installer_name("0.2.0", "x86")


def test_installer_ui_theme_has_what_the_surface_uses() -> None:
    # The Win32 surface compiles only on Windows; its colours and sizes come from DESIGN.md.
    header = build_installer_ui.theme_header()
    fields = re.findall(r"COLORREF (\w+);", header)
    for mode in ("kLight", "kDark"):
        values = re.search(rf"{mode} = \{{(.*)\}};", header)
        assert values
        assert values.group(1).count("RGB(") == len(fields)
    source = (build_installer_ui.SOURCE / "setup_ui.cpp").read_text(encoding="utf-8")
    assert set(re.findall(r"palette\.(\w+)", source)) <= set(fields)
    constants = set(re.findall(r"inline constexpr \w+ (k\w+) =", header))
    assert set(re.findall(r"\b(k[A-Z]\w+)\b", source)) <= constants | {"kLight", "kDark"}
    assert "inline constexpr int kButtonHeight = 32;" in header


@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("makensis") is None,
    reason="needs NSIS and the Windows MSVC installer UI toolchain",
)
@pytest.mark.parametrize("arch", ["x64", "arm64"])
def test_installer_compiles(tmp_path: Path, arch: str) -> None:
    makensis = Path(shutil.which("makensis") or "")
    directory = _install_directory(tmp_path, build_windows.target(arch)[1])
    installer = build_windows.build_installer(directory, "0.2.0", makensis, tmp_path / "out", arch)
    assert installer.name == f"Solaris-0.2.0-windows-{arch}-setup.exe"
    assert installer.read_bytes()[:2] == b"MZ"


try:
    MAKENSIS = build_windows.find_makensis(None)
except BuildError:
    MAKENSIS = None
REQUIRES_NSIS = pytest.mark.skipif(MAKENSIS is None, reason="needs NSIS")


def build_harness(directory: Path) -> tuple[Path, Path]:
    payload = directory / "payload"
    module = payload / "google" / "_upb" / "_message.pyd"
    module.parent.mkdir(parents=True)
    module.write_bytes(b"old extension module")
    (payload / "Solaris.exe").write_bytes(b"stand-in, never executed")
    owned = directory / "remove-payload.nsh"
    owned.write_text(build_windows.uninstall_list(payload), encoding="utf-8-sig")
    installed = directory / "Solaris test $folder"
    installer = directory / "cleanup-fixture.exe"
    script = directory / "harness.nsi"
    source = r"""
Unicode true
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
!include LogicLib.nsh
LoadLanguageFile "${NSISDIR}\Contrib\Language files\English.nlf"
!macro Fail MESSAGE CODE
  FileOpen $9 "$INSTDIR\cleanup-error.txt" w
  FileWriteUTF16LE /BOM $9 "${MESSAGE}$\r$\n"
  FileClose $9
  SetErrorLevel ${CODE}
  Abort
!macroend
!macro RemovalFailed PATH
  !insertmacro Fail "Could not remove installed file: ${PATH}" 1603
!macroend
!include "@INCLUDE@"
Name "Solaris cleanup test"
OutFile "@OUTPUT@"
InstallDir "@INSTALLED@"
Section
  SetOutPath "$INSTDIR"
  File /r "@PAYLOAD@\*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd
Section "Uninstall"
  !include "@OWNED@"
SectionEnd
"""
    for name, path in {
        "INCLUDE": build_windows.PACKAGE / "remove-files.nsh",
        "OUTPUT": installer,
        "INSTALLED": installed,
        "PAYLOAD": payload,
        "OWNED": owned,
    }.items():
        source = source.replace(f"@{name}@", build_windows.nsis_string(str(path)))
    script.write_text(source, encoding="utf-8-sig")
    compiled = subprocess.run(
        [str(MAKENSIS), "-V2", "-WX", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return installer, installed


@REQUIRES_NSIS
def test_removal_routine_compiles(tmp_path: Path) -> None:
    installer, _ = build_harness(tmp_path)
    assert installer.read_bytes()[:2] == b"MZ"


def install_harness(directory: Path) -> Path:
    installer, installed = build_harness(directory)
    subprocess.run([str(installer), "/S"], check=True, timeout=20)
    return installed


def uninstall(installed: Path) -> subprocess.CompletedProcess[bytes]:
    # NSIS requires _?= to be the last, unquoted part of its Windows command line.
    return subprocess.run(
        f'"{installed / "uninstall.exe"}" /S _?={installed}',
        timeout=20,
        check=False,
    )


@pytest.mark.skipif(
    sys.platform != "win32", reason="needs Windows file attributes and NSIS runtime"
)
@REQUIRES_NSIS
def test_removes_readonly_owned_files_and_preserves_unknown_files(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    installed = install_harness(tmp_path)
    old = installed / "google" / "_upb" / "_message.pyd"
    old.chmod(stat.S_IREAD)
    extra = old.parent / "user-note.txt"
    extra.write_text("keep me", encoding="utf-8")
    extra.chmod(stat.S_IREAD)
    try:
        assert uninstall(installed).returncode == 0
        assert not old.exists()
        assert not (installed / "Solaris.exe").exists()
        assert extra.read_text(encoding="utf-8") == "keep me"
        assert extra.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        extra.chmod(stat.S_IWRITE)


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows file locking and NSIS runtime")
@REQUIRES_NSIS
def test_locked_old_extension_fails_then_retry_cleans_it(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows only")
    from ctypes import wintypes

    installed = install_harness(tmp_path)
    old = installed / "google" / "_upb" / "_message.pyd"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(old), 0x80000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        assert uninstall(installed).returncode == 1603
        assert old.exists()
        assert "_message.pyd" in (installed / "cleanup-error.txt").read_text(encoding="utf-16")
    finally:
        kernel.CloseHandle(handle)
    assert uninstall(installed).returncode == 0
    assert not old.exists()
    # A previously removed file is harmless on retry.
    assert not (installed / "Solaris.exe").exists()
