"""scripts/build_windows.py: the install directory's checks, the uninstall list and the NSIS
compile. The real build runs on Windows in CI; here the installer is compiled around a stand-in
directory, when makensis is installed."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import build_installer_ui, build_windows
from scripts.build_apple import BuildError
from solaris.desktop.links import SCHEMES


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
def test_only_the_arm64_gui_build_scans_dependencies_with_the_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arch: str
) -> None:
    commands: list[list[str | Path]] = []
    monkeypatch.setattr(build_windows, "python_architecture", lambda: arch)
    monkeypatch.setattr(build_windows, "build_directory", lambda arch: tmp_path / arch)
    monkeypatch.setattr(build_windows.build_app, "google_client", lambda directory: [])
    monkeypatch.setattr(
        build_windows.build_apple, "_run", lambda command, **_: commands.append(command)
    )
    build_windows.build_ui("1.2.3", arch)
    plugins = [part for part in map(str, commands[0]) if part.startswith("--user-plugin=")]
    assert plugins == ([f"--user-plugin={build_windows.PEFILE_PLUGIN}"] if arch == "arm64" else [])
    assert build_windows.PEFILE_PLUGIN.is_file()


def test_the_dependency_plugin_is_written_for_the_builds_nuitka() -> None:
    # It replaces a Nuitka internal and stops any other Nuitka; tests/test_nuitka_pefile.py
    # compares it with the original under that Nuitka.
    source = build_windows.PEFILE_PLUGIN.read_text(encoding="utf-8")
    (version,) = re.findall(r'^NUITKA_VERSION = "([^"]+)"$', source, re.MULTILINE)
    assert version == build_windows.build_app.NUITKA_VERSION
    for name in ("ci.yml", "release.yml"):
        workflow = (build_windows.ROOT / ".github" / "workflows" / name).read_text("utf-8")
        assert set(re.findall(r"--with nuitka==(\S+)", workflow)) == {version}


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


@pytest.mark.parametrize("signed", [False, True])
def test_a_signed_release_links_the_service_with_signed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed: bool
) -> None:
    commands: list[list[str | Path]] = []
    monkeypatch.setattr(build_windows, "BUILD", tmp_path)
    monkeypatch.setattr(
        build_windows.build_apple, "_run", lambda command, **_: commands.append(command)
    )
    build_windows.build_service("1.2.3", "x64", signed)
    (ldflags,) = [str(part) for part in commands[0] if str(part).startswith("-ldflags=")]
    assert f"-X {build_windows.build_apple.GO_VERSION_VARIABLE}=1.2.3" in ldflags
    assert (f"-X {build_windows.SIGNED_RELEASE_VARIABLE}=true" in ldflags) is signed
    assert build_windows.SIGNED_RELEASE_VARIABLE.endswith("/internal/product.SignedRelease")


@pytest.mark.parametrize("signed", [False, True])
def test_a_signed_installer_signs_its_plugin_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signed: bool
) -> None:
    # The setup UI's plugin is signed before makensis packs it; the installer script gets the
    # signing script for !uninstfinalize and !finalize.
    plugin = tmp_path / "ui" / "SolarisSetupUI.dll"
    commands: list[list[str]] = []

    def run(command: list[str | Path], **_: object) -> None:
        commands.append([str(part) for part in command])
        if Path(command[0]).name == "makensis":
            (tmp_path / "out" / build_windows.installer_name("0.2.0")).write_bytes(b"MZ")

    monkeypatch.setattr(build_windows.build_installer_ui, "build", lambda output: plugin)
    monkeypatch.setattr(build_windows.build_apple, "_run", run)
    directory = _install_directory(tmp_path)
    build_windows.build_installer(
        directory, "0.2.0", Path("makensis"), tmp_path / "out", "x64", signed
    )
    signing = [command for command in commands if str(build_windows.SIGN_SCRIPT) in command]
    assert signing == ([commands[0]] if signed else [])
    if signed:
        assert commands[0][0] == "powershell.exe"
        assert commands[0][-1] == str(plugin)
    wrapper = (tmp_path / "out" / "nsis" / "installer.nsi").read_text(encoding="utf-8-sig")
    defined = f'!define SIGN_SCRIPT "{build_windows.nsis_string(str(build_windows.SIGN_SCRIPT))}"'
    assert (defined in wrapper) is signed


def test_the_installer_script_signs_both_programs_or_fails() -> None:
    script = build_windows.INSTALLER_SCRIPT.read_text(encoding="utf-8")
    block = script[script.index("!ifdef SIGN_SCRIPT") :]
    block = block[: block.index("!endif")]
    command = (
        "'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        '-File "${SIGN_SCRIPT}" "%1"\' = 0'
    )
    assert f"!uninstfinalize {command}" in block
    assert f"!finalize {command}" in block


def _macro(script: str, name: str) -> str:
    start = script.index(f"!macro {name} ")
    return script[start : script.index("!macroend", start)]


def test_the_installer_registers_every_install_link_scheme() -> None:
    # One-click import (D6, D45): every scheme links.py takes, as the smoke test checks.
    script = build_windows.INSTALLER_SCRIPT.read_text(encoding="utf-8")
    registered = re.findall(r"^\s*!insertmacro RegisterScheme (\S+)$", script, re.MULTILINE)
    unregistered = re.findall(r"^\s*!insertmacro UnregisterScheme (\S+)$", script, re.MULTILINE)
    assert registered == unregistered == list(SCHEMES)
    # Installing writes HKLM over another client's registration there, opening this install.
    register = _macro(script, "RegisterScheme")
    command = "Software\\Classes\\${SCHEME}\\shell\\open\\command"
    assert f'WriteRegStr HKLM "{command}" "" \'${{LINK_COMMAND}}\'' in register
    assert 'WriteRegStr HKLM "Software\\Classes\\${SCHEME}" "URL Protocol" ""' in register
    assert '!define LINK_COMMAND \'"$INSTDIR\\${CLIENT}" "%1"\'' in script
    # Uninstalling deletes only a key that still opens this installation; an upgrade keeps it.
    unregister = _macro(script, "UnregisterScheme")
    assert f'ReadRegStr $0 HKLM "{command}" ""' in unregister
    assert "${If} $0 == '${LINK_COMMAND}'" in unregister
    assert 'DeleteRegKey HKLM "Software\\Classes\\${SCHEME}"' in unregister
    assert "HKCU" not in register + unregister  # the user's own: the UI's (associations.py)
    uninstall = script[script.index('Section "!un.${PRODUCT}"') :]
    upgrade_returns = uninstall.index("Return")
    assert all(uninstall.index(f"UnregisterScheme {s}") > upgrade_returns for s in SCHEMES)
    # The CI smoke test checks the same list on a real install and uninstall.
    smoke_test = (build_windows.SIGN_SCRIPT.parent / "smoke-test.ps1").read_text(encoding="utf-8")
    (listed,) = re.findall(r"^\$Schemes = @\((.*)\)$", smoke_test, re.MULTILINE)
    assert [name.strip().strip("'") for name in listed.split(",")] == list(SCHEMES)


@REQUIRES_NSIS
@pytest.mark.parametrize("status", [0, 1])
def test_nsis_finalize_signs_the_uninstaller_then_the_installer(
    tmp_path: Path, status: int
) -> None:
    # What installer.nsi relies on: !uninstfinalize runs on the uninstaller before it goes
    # into the installer, !finalize on the installer, and "= 0" fails the compile otherwise.
    log = tmp_path / "signed.txt"
    signer = tmp_path / "signer.py"
    signer.write_text(
        "import sys\n"
        f"open({str(log)!r}, 'a', encoding='utf-8').write(sys.argv[1] + '\\n')\n"
        f"sys.exit({status})\n",
        encoding="utf-8",
    )
    installer = tmp_path / "finalized.exe"
    command = f'{sys.executable} "{signer}" "%1"'
    script = tmp_path / "finalize.nsi"
    script.write_text(
        "Unicode true\n"
        "RequestExecutionLevel user\n"
        f'OutFile "{build_windows.nsis_string(str(installer))}"\n'
        f"!uninstfinalize '{command}' = 0\n"
        f"!finalize '{command}' = 0\n"
        'Section\n  WriteUninstaller "$INSTDIR\\uninstall.exe"\nSectionEnd\n'
        'Section "Uninstall"\nSectionEnd\n',
        encoding="utf-8-sig",
    )
    compiled = subprocess.run(
        [str(MAKENSIS), "-V2", "-WX", str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={**os.environ, "LC_ALL": "en_US.UTF-8"},
    )
    signed = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    if status == 0:
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        assert len(signed) == 2
        assert Path(signed[1]) == installer
        assert Path(signed[0]) != installer  # the uninstaller, before it went in
    else:
        assert compiled.returncode != 0
        assert len(signed) == 1  # the uninstaller's failure stopped the compile


SIGN = (
    "powershell.exe",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    str(build_windows.SIGN_SCRIPT),
)


def _sign_script(*arguments: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    done = subprocess.run(
        [*SIGN, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
    )
    return done.returncode, done.stdout + done.stderr


ON_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Authenticode is on Windows")


@ON_WINDOWS
def test_sign_script_checks_signer_and_usage(tmp_path: Path) -> None:
    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(_pe(build_windows.PE_MACHINE_AMD64))
    # Not a whole PE: Windows has no signature to read in it ("UnknownError").
    status, output = _sign_script("-Check", str(unsigned))
    assert status == 1, output
    assert output.startswith(f"::error::{unsigned}: "), output
    # A copy of a system program is valid here through Windows' catalogs, not on its own.
    copied = tmp_path / "where.exe"
    shutil.copyfile(Path(os.environ["SYSTEMROOT"]) / "System32" / "where.exe", copied)
    status, output = _sign_script("-Check", str(unsigned), str(copied))
    assert status == 1, output
    assert "signed only in this machine's Catalog" in output
    # Microsoft's signature is one Windows trusts, but not Solaris's.
    programs = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    edge = programs / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if not edge.is_file():
        pytest.skip("no signed program to check")
    status, output = _sign_script("-Check", str(edge))
    assert status == 1, output
    assert "signed by 'Microsoft Corporation', not 'Herta Network LLC'" in output


def test_smoke_test_names_each_file_to_check_on_its_own() -> None:
    """smoke-test.ps1 runs in Windows PowerShell, which joins an array given to sign.ps1's
    remaining arguments into one string."""
    smoke_test = (build_windows.SIGN_SCRIPT.parent / "smoke-test.ps1").read_text(encoding="utf-8")
    assert "-Check $Installer $Uninstaller $Client $ServiceExecutable" in smoke_test


@ON_WINDOWS
def test_sign_script_checks_each_file_a_windows_powershell_script_names(tmp_path: Path) -> None:
    first, second = tmp_path / "with space.exe", tmp_path / "second.exe"
    for program in (first, second):
        program.write_bytes(_pe(build_windows.PE_MACHINE_AMD64))
    command = f"& '{build_windows.SIGN_SCRIPT}' -Check '{first}' '{second}'; exit $LASTEXITCODE"
    done = subprocess.run(
        [*SIGN[:-2], "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    output = done.stdout + done.stderr
    assert done.returncode == 1, output
    assert f"::error::{first}: " in output, output
    assert f"::error::{second}: " in output, output


@ON_WINDOWS
def test_sign_script_needs_the_release_signing_setup(tmp_path: Path) -> None:
    program = tmp_path / "Solaris.exe"
    program.write_bytes(_pe(build_windows.PE_MACHINE_AMD64))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"SIGNTOOL", "ARTIFACT_SIGNING_DLIB", "ARTIFACT_SIGNING_METADATA"}
    }
    status, output = _sign_script(str(program), env=environment)
    assert status != 0
    assert "SIGNTOOL names no file" in output
    status, output = _sign_script("-Directory", str(tmp_path), env=environment)
    assert status != 0
    assert "has no solaris-service.exe" in output


def _workflow(name: str) -> dict[str, Any]:
    path = build_windows.ROOT / ".github" / "workflows" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_only_the_release_workflow_signs() -> None:
    # The release's Windows job signs in to Azure between the build and the installer, and
    # passes --sign to both; CI's builds stay unsigned, without SignedRelease (D42).
    job = _workflow("release.yml")["jobs"]["windows"]
    assert job["environment"] == "release"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    steps = job["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    (build,) = [index for index, run in enumerate(runs) if "--skip-installer" in run]
    (installer,) = [index for index, run in enumerate(runs) if "--installer-only" in run]
    (login,) = [
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("azure/login@")
    ]
    assert build < login < installer
    assert "--sign" in runs[build].split()
    assert "--sign" in runs[installer].split()
    assert re.fullmatch(r"azure/login@[0-9a-f]{40}", steps[login]["uses"])
    # The identity has no role in the subscription: azure/login would fail to select it.
    assert steps[login]["with"]["allow-no-subscriptions"] is True
    assert "subscription-id" not in steps[login]["with"]
    (smoke,) = [run for run in runs if "smoke-test.ps1" in run]
    assert "-Signed" in smoke.split()
    ci = (build_windows.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for signing in ("--sign", "-Signed", "id-token", "azure/login", "ARTIFACT_SIGNING"):
        assert signing not in ci
