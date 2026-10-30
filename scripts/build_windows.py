"""Build the Windows installer (docs/architecture.md §5.1, §9, §10): the UI by Nuitka in
standalone mode, the service by go build, the GEO data beside it, then the NSIS installer
around the three.

    uv run --with nuitka==4.2.2 python scripts/build_windows.py                    # all
    uv run --with nuitka==4.2.2 python scripts/build_windows.py --skip-installer   # directory
    uv run python scripts/build_windows.py --installer-only                        # installer

Everything lands in build/windows:

    Solaris/                                  the install directory, as the installer lays it out
    Solaris-<version>-windows-x64-setup.exe

The installer is package/windows/installer.nsi, compiled by makensis (NSIS 3.08 or later)
against Solaris/ and an uninstall list of exactly its files; package/windows/setup.ps1 is the
part that closes the UI and registers, stops and removes the service. wintun is not shipped on
its own: sing-tun embeds wintun.dll in the service and loads it from memory.

Nothing is signed yet: the Windows signing scheme is pending (docs/roadmap.md). When it is
chosen, the release workflow signs the executables and DLLs in Solaris/ between
--skip-installer and --installer-only (the service checks that the UI has its signer), and the
installer and its uninstaller through makensis' !finalize and !uninstfinalize. Nothing here
installs or runs anything; package/windows/smoke-test.ps1 does that on a disposable CI runner.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as a script, Python puts scripts/ on the path rather than the root.
sys.path.insert(0, str(ROOT))

from scripts import build_app, build_apple, fetch_geodata  # noqa: E402
from scripts.build_apple import BuildError  # noqa: E402

BUILD = ROOT / "build" / "windows"
PACKAGE = ROOT / "package" / "windows"
ENTRY = ROOT / "package" / "entry.py"
CORE = ROOT / "src" / "core"
INSTALLER_SCRIPT = PACKAGE / "installer.nsi"
SETUP_SCRIPT = PACKAGE / "setup.ps1"

APP_NAME = "Solaris"
# The only client the service accepts, from its own directory (src/core/internal/product).
CLIENT = "Solaris.exe"
SERVICE = "solaris-service.exe"
GEODATA = "geodata"  # beside the service, under mihomo's names
# All four TUN stacks (D3); the version is what GetInfo reports.
GO_TAGS = "with_gvisor"
# The files an installed Solaris cannot do without; check() looks for each.
REQUIRED = (
    CLIENT,
    SERVICE,
    "LICENSE",
    "solaris/licenses/THIRD_PARTY_NOTICES.txt",
    *(f"{GEODATA}/{name}" for name in build_apple.GEO_FILES),
)
PE_SUFFIXES = (".exe", ".dll", ".pyd")
PE_MACHINE_AMD64 = 0x8664  # D2: x64 only

# The UI shows SVG; PNG is built in. Qt names its image format plugins q<name>.dll.
UNUSED_IMAGE_FORMATS = ("gif", "icns", "ico", "jpeg", "pdf", "tga", "tiff", "wbmp", "webp")
# The Qt modules and QML directories the UI never loads, as on macOS; the libraries are named
# Qt6X.dll here and QtX for the bindings. Python's ssl module needs the libssl and libcrypto
# DLLs CPython ships, so only Qt's OpenSSL backend goes: Qt's TLS is Schannel.
EXCLUDED = (
    *(f"*Qt{name}*" for name in build_app.UNUSED_QT_LIBRARIES),
    *(f"*Qt6{name}*" for name in build_app.UNUSED_QT_LIBRARIES),
    *(f"*/qml/{directory}/*" for directory in build_app.UNUSED_QML_DIRECTORIES),
    *(f"*/imageformats/q{name}.dll" for name in UNUSED_IMAGE_FORMATS),
    "*qopensslbackend*",
)


def installer_name(version: str) -> str:
    """As docs/release.md §1 names it, which the UI's updater looks for."""
    return f"{APP_NAME}-{version}-windows-x64-setup.exe"


def build_service(version: str) -> Path:
    """solaris-service.exe for windows/amd64, without cgo, symbols or build paths."""
    output = BUILD / "service" / SERVICE
    output.unlink(missing_ok=True)
    environment = os.environ | {
        "GOPROXY": build_apple.GOPROXY,
        "GOOS": "windows",
        "GOARCH": "amd64",
        "CGO_ENABLED": "0",
    }
    command: list[str | Path] = [
        "go",
        "-C",
        CORE,
        "build",
        "-trimpath",
        "-tags",
        GO_TAGS,
        f"-ldflags=-s -w -X {build_apple.GO_VERSION_VARIABLE}={version}",
        "-o",
        output,
        "./service",
    ]
    build_apple._run(command, env=environment)
    return output


def build_ui(version: str) -> Path:
    """The Nuitka standalone directory of package/entry.py; its own options are in that file."""
    output = BUILD / "nuitka"
    if output.exists():
        shutil.rmtree(output)
    command: list[str | Path] = [
        sys.executable,
        "-m",
        "nuitka",
        # Never onefile: the service accepts the UI only from its install directory.
        "--mode=standalone",
        "--msvc=latest",
        "--enable-plugins=pyside6",
        "--include-qt-plugins=qml",
        # The Nuitka plugin pulls in the QtWidgets binding for QtQuick; the UI never imports it.
        "--nofollow-import-to=PySide6.QtWidgets",
        "--windows-console-mode=disable",
        f"--output-filename={Path(CLIENT).stem}",
        f"--product-name={APP_NAME}",
        f"--file-description={APP_NAME}",
        f"--product-version={version}",
        f"--file-version={version}",
        f"--output-dir={output}",
        "--assume-yes-for-downloads",
        *(f"--noinclude-dlls={pattern}" for pattern in EXCLUDED),
        *(f"--noinclude-data-files={pattern}" for pattern in EXCLUDED),
        ENTRY,
    ]
    build_apple._run(command)
    return output / f"{ENTRY.stem}.dist"


def assemble(ui: Path, service: Path, geodata: dict[str, Path]) -> Path:
    """The install directory: the UI's standalone directory with the service beside Solaris.exe
    and the GEO data in geodata/."""
    directory = BUILD / APP_NAME
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(ui, directory)
    shutil.copyfile(service, directory / SERVICE)
    (directory / GEODATA).mkdir()
    for name in build_apple.GEO_FILES:
        shutil.copyfile(geodata[name], directory / GEODATA / name)
    return directory


def pe_machine(path: Path) -> int | None:
    """The machine field of a PE file's COFF header; None when it is not a PE file."""
    with path.open("rb") as file:
        header = file.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            return None
        file.seek(int.from_bytes(header[60:64], "little"))
        signature = file.read(6)
    if len(signature) < 6 or signature[:4] != b"PE\0\0":
        return None
    return int.from_bytes(signature[4:6], "little")


def check(directory: Path) -> int:
    """Every required file is there, nothing is a link, and every executable, DLL and
    extension module is x64. Returns how many of those there are."""
    for name in REQUIRED:
        if not (directory / name).is_file():
            raise BuildError(f"{directory.name} has no {name}")
    count = 0
    for path in sorted(directory.rglob("*")):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise BuildError(f"{name} is a link; the installer copies files")
        if not path.is_file() or path.suffix.lower() not in PE_SUFFIXES:
            continue
        machine = pe_machine(path)
        if machine != PE_MACHINE_AMD64:
            shown = "not a PE file" if machine is None else f"machine {machine:#06x}"
            raise BuildError(f"{name} is {shown}, not x64")
        count += 1
    return count


def nsis_string(value: str) -> str:
    """``value`` for the inside of a double-quoted NSIS string."""
    if "\n" in value or "\r" in value:
        raise BuildError(f"line break in {value!r}")
    return value.replace("$", "$$").replace('"', '$\\"')


def _deepest_first(directory: Path) -> Iterator[Path]:
    # A path sorts before everything under it, so the reverse order puts each directory
    # after its contents.
    yield from sorted(directory.rglob("*"), reverse=True)


def uninstall_list(directory: Path) -> str:
    """NSIS lines that remove exactly the files and directories in ``directory`` from
    $INSTDIR, contents before their directories; the uninstaller never removes a directory
    recursively, so files it did not install stay."""
    lines = []
    for path in _deepest_first(directory):
        relative = nsis_string(str(path.relative_to(directory)).replace("/", "\\"))
        action = "RMDir" if path.is_dir() else "Delete"
        lines.append(f'{action} "$INSTDIR\\{relative}"')
    return "\n".join(lines) + "\n"


def find_makensis(given: str | None) -> Path:
    candidates = [given] if given else ["makensis"]
    if not given and (programs := os.environ.get("PROGRAMFILES(X86)")):
        candidates.append(str(Path(programs) / "NSIS" / "makensis.exe"))
    for candidate in candidates:
        if found := shutil.which(candidate):
            return Path(found)
    raise BuildError("makensis (NSIS 3) is missing; install NSIS or pass --makensis")


def build_installer(directory: Path, version: str, makensis: Path, output: Path) -> Path:
    """Compile installer.nsi against ``directory`` into ``output``/<installer name>. The
    definitions and the uninstall list go into ``output``/nsis."""
    work = output / "nsis"
    work.mkdir(parents=True, exist_ok=True)
    files = work / "uninstall-files.nsh"
    # makensis reads a file without a byte order mark in the system's code page, unless an
    # !include names the charset: the generated files get the mark, installer.nsi the option.
    files.write_text(uninstall_list(directory), encoding="utf-8-sig")
    installer = output / installer_name(version)
    installer.unlink(missing_ok=True)
    definitions = {
        "APP_DIRECTORY": directory,
        "APP_VERSION": version,
        "OUTPUT_FILE": installer,
        "SETUP_SCRIPT": SETUP_SCRIPT,
        "UNINSTALL_FILES": files,
    }
    wrapper = work / "installer.nsi"
    lines = [f'!define {key} "{nsis_string(str(value))}"' for key, value in definitions.items()]
    lines.append(f'!include /CHARSET=UTF8 "{nsis_string(str(INSTALLER_SCRIPT))}"')
    wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    # Any warning fails the build.
    build_apple._run([makensis, "-V2", "-WX", wrapper])
    if not installer.is_file():
        raise BuildError(f"makensis wrote no {installer.name}")
    return installer


def _check_nuitka() -> None:
    try:
        installed = importlib.metadata.version("nuitka")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != build_app.NUITKA_VERSION:
        version = build_app.NUITKA_VERSION
        sys.exit(f"build_windows: run with uv run --with nuitka=={version} (found {installed})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Windows installer.")
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument(
        "--skip-installer", action="store_true", help=f"stop at build/windows/{APP_NAME}"
    )
    stage.add_argument(
        "--installer-only",
        action="store_true",
        help=f"compile the installer from an existing build/windows/{APP_NAME}",
    )
    parser.add_argument("--makensis", help="the NSIS compiler (default: on PATH)")
    arguments = parser.parse_args()
    if sys.platform != "win32":
        sys.exit("build_windows: the Windows installer is built on Windows")
    version = build_apple._version()
    directory = BUILD / APP_NAME
    try:
        makensis = None if arguments.skip_installer else find_makensis(arguments.makensis)
        if not arguments.installer_only:
            _check_nuitka()
            geodata = fetch_geodata.fetch()
            service = build_service(version)
            ui = build_ui(version)
            directory = assemble(ui, service, geodata)
        count = check(directory)
        size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        shown = directory.relative_to(ROOT)
        print(f"build_windows: {shown}, {count} x64 binaries, {size / 2**20:.1f} MiB")
        if makensis is not None:
            installer = build_installer(directory, version, makensis, BUILD)
            megabytes = installer.stat().st_size / 2**20
            print(f"build_windows: {installer.relative_to(ROOT)}, {megabytes:.1f} MiB (unsigned)")
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"build_windows: {error}")


if __name__ == "__main__":
    main()
