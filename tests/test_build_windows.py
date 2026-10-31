"""scripts/build_windows.py: the install directory's checks, the uninstall list and the NSIS
compile. The real build runs on Windows in CI; here the installer is compiled around a stand-in
directory, when makensis is installed."""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

import pytest

from scripts import build_windows
from scripts.build_apple import BuildError


def _pe(machine: int) -> bytes:
    """A minimal PE header: the DOS stub's pointer at 0x3C, then the signature and machine."""
    return b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0" + struct.pack("<H", machine)


def _install_directory(root: Path) -> Path:
    directory = root / "Solaris"
    for name in build_windows.REQUIRED:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            _pe(build_windows.PE_MACHINE_AMD64) if name.endswith((".exe", ".dll")) else b"x"
        )
    qml = directory / "PySide6" / "qml" / "QtQuick"
    qml.mkdir(parents=True)
    (qml / "qtquick2plugin.dll").write_bytes(_pe(build_windows.PE_MACHINE_AMD64))
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


def test_check_wants_every_required_file(tmp_path: Path) -> None:
    directory = _install_directory(tmp_path)
    (directory / "geodata" / "GeoIP.dat").unlink()
    with pytest.raises(BuildError, match=r"GeoIP\.dat"):
        build_windows.check(directory)


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
    assert 'Delete "$INSTDIR\\Solaris.exe"' in lines
    assert 'Delete "$INSTDIR\\PySide6\\qml\\QtQuick\\price$$1.qml"' in lines
    assert all(line.startswith(("Delete ", "RMDir ")) for line in lines)
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


@pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("makensis") is None,
    reason="needs NSIS and the Windows MSVC installer UI toolchain",
)
def test_installer_compiles(tmp_path: Path) -> None:
    makensis = Path(shutil.which("makensis") or "")
    directory = _install_directory(tmp_path)
    installer = build_windows.build_installer(directory, "0.2.0", makensis, tmp_path / "out")
    assert installer.name == "Solaris-0.2.0-windows-x64-setup.exe"
    assert installer.read_bytes()[:2] == b"MZ"
