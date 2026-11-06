"""Build the Windows installer (docs/architecture.md §5.1, §9, §10): the UI by Nuitka in
standalone mode, the service by go build, the GEO data beside it, then the NSIS installer
around the three.

    uv run --with nuitka==4.2.2 python scripts/build_windows.py                    # all
    uv run --with nuitka==4.2.2 python scripts/build_windows.py --skip-installer   # directory
    uv run python scripts/build_windows.py --installer-only                        # installer
    ... --sign                                                                     # a release

Everything lands in build/windows:

    Solaris/                                  the install directory, as the installer lays it out
    Solaris-<version>-windows-x64-setup.exe

Native Windows ARM64 builds use --arch arm64 and build/windows-arm64 instead.
The GUI must be built with a Python interpreter of the selected architecture.

The installer is package/windows/installer.nsi, compiled by makensis (NSIS 3.08 or later)
against Solaris/ and an uninstall list of exactly its files; package/windows/setup.ps1 is the
part that closes the UI and registers, stops and removes the service. wintun is not shipped on
its own: sing-tun embeds wintun.dll in the service and loads it from memory.

Only the release workflow signs, with --sign (Azure Artifact Signing, docs/decisions.md D42):
the service links with SignedRelease, so that it accepts only a UI with Solaris's signature
(docs/architecture.md §5.3), and compiling the installer first has package/windows/sign.ps1
sign the executables, DLLs and extension modules in Solaris/ that carry no publisher's
signature, then the setup UI's plugin, then through makensis' !uninstfinalize and !finalize
the uninstaller and the installer. The release workflow builds with --skip-installer --sign,
signs in to Azure, then runs --installer-only --sign: the sign-in stays fresh. Without --sign
nothing is signed, as in CI. Nothing here installs or runs anything;
package/windows/smoke-test.ps1 does that on a disposable CI runner. The Google OAuth client
comes from the environment as for scripts/build_app.py.
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

from scripts import build_app, build_apple, build_installer_ui, cronet, fetch_geodata, symbols  # noqa: E402
from scripts.build_apple import BuildError  # noqa: E402

BUILD = ROOT / "build" / "windows"
PACKAGE = ROOT / "package" / "windows"
ENTRY = ROOT / "package" / "entry.py"
CORE = ROOT / "src" / "core"
INSTALLER_SCRIPT = PACKAGE / "installer.nsi"
ICON = ROOT / "package" / "icons" / "solaris.ico"  # scripts/gen_icons.py
SETUP_SCRIPT = PACKAGE / "setup.ps1"
SIGN_SCRIPT = PACKAGE / "sign.ps1"
SIGNED_RELEASE_VARIABLE = "github.com/Ember-Moth/Solaris/src/core/internal/product.SignedRelease"

APP_NAME = "Solaris"
# The only client the service accepts, from its own directory (src/core/internal/product).
CLIENT = "Solaris.exe"
SERVICE = "solaris-service.exe"
GEODATA = "geodata"  # beside the service, under mihomo's names
# All four TUN stacks (D3); the version is what GetInfo reports.
GO_TAGS = "with_gvisor,with_naive,with_purego"
# The files an installed Solaris cannot do without; check() looks for each.
REQUIRED = (
    CLIENT,
    SERVICE,
    "libcronet.dll",
    "LICENSE",
    "solaris/licenses/THIRD_PARTY_NOTICES.txt",
    *(f"{GEODATA}/{name}" for name in build_apple.GEO_FILES),
)
PE_SUFFIXES = (".exe", ".dll", ".pyd")
PE_MACHINE_AMD64 = 0x8664
PE_MACHINE_ARM64 = 0xAA64
TARGETS = {"x64": ("amd64", PE_MACHINE_AMD64), "arm64": ("arm64", PE_MACHINE_ARM64)}


def target(arch: str) -> tuple[str, int]:
    try:
        return TARGETS[arch]
    except KeyError:
        raise BuildError(f"unsupported Windows architecture: {arch}") from None


def build_directory(arch: str) -> Path:
    target(arch)
    return BUILD if arch == "x64" else ROOT / "build" / "windows-arm64"


def python_architecture() -> str:
    machine = pe_machine(Path(sys.executable))
    for arch, (_, expected) in TARGETS.items():
        if machine == expected:
            return arch
    raise BuildError("the Windows build needs native x64 or ARM64 Python")


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


def installer_name(version: str, arch: str = "x64") -> str:
    """As docs/release.md §1 names it, which the UI's updater looks for."""
    target(arch)
    return f"{APP_NAME}-{version}-windows-{arch}-setup.exe"


def build_service(version: str, arch: str = "x64", signed: bool = False) -> Path:
    """Native Windows service, with DWARF for retained crash symbols. Linked ``signed``, as a
    release is, it accepts only a UI with Solaris's signature."""
    output = build_directory(arch) / "service" / SERVICE
    output.unlink(missing_ok=True)
    environment = os.environ | {
        "GOPROXY": build_apple.GOPROXY,
        "GOOS": "windows",
        "GOARCH": target(arch)[0],
        "CGO_ENABLED": "0",
    }
    variables = f"-X {build_apple.GO_VERSION_VARIABLE}={version}"
    if signed:
        variables += f" -X {SIGNED_RELEASE_VARIABLE}=true"
    command: list[str | Path] = [
        "go",
        "-C",
        CORE,
        "build",
        "-trimpath",
        "-tags",
        GO_TAGS,
        f"-ldflags={variables}",
        "-o",
        output,
        "./service",
    ]
    build_apple._run(command, env=environment)
    return output


def build_ui(version: str, arch: str = "x64") -> Path:
    """The Nuitka standalone directory of package/entry.py; its own options are in that file."""
    if python_architecture() != arch:
        raise BuildError(f"use native {arch} Python to build the {arch} GUI")
    build = build_directory(arch)
    output = build / "nuitka"
    if output.exists():
        shutil.rmtree(output)
    # Qt's 6.12 ARM64 wheel carries an unused ARM32 C++/CX runtime. None of the
    # retained Qt libraries imports it; keep it out of the native ARM64 payload.
    excluded = EXCLUDED + (("*vccorlib140.dll",) if arch == "arm64" else ())
    command: list[str | Path] = [
        sys.executable,
        "-m",
        "nuitka",
        # Never onefile: the service accepts the UI only from its install directory.
        "--mode=standalone",
        "--unstripped",
        "--msvc=latest",
        "--enable-plugins=pyside6",
        "--include-qt-plugins=qml",
        # The Nuitka plugin pulls in the QtWidgets binding for QtQuick; the UI never imports it.
        "--nofollow-import-to=PySide6.QtWidgets",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ICON}",
        f"--output-filename={Path(CLIENT).stem}",
        f"--product-name={APP_NAME}",
        f"--file-description={APP_NAME}",
        f"--product-version={version}",
        f"--file-version={version}",
        f"--output-dir={output}",
        "--assume-yes-for-downloads",
        *(f"--noinclude-dlls={pattern}" for pattern in excluded),
        *(f"--noinclude-data-files={pattern}" for pattern in excluded),
        *build_app.google_client(build),
        ENTRY,
    ]
    build_apple._run(command)
    return output / f"{ENTRY.stem}.dist"


def assemble(ui: Path, service: Path, geodata: dict[str, Path], arch: str = "x64") -> Path:
    """The install directory: the UI's standalone directory with the service beside Solaris.exe
    and the GEO data in geodata/."""
    directory = build_directory(arch) / APP_NAME
    if directory.exists():
        shutil.rmtree(directory)
    shutil.copytree(ui, directory)
    shutil.copyfile(service, directory / SERVICE)
    shutil.copyfile(cronet.library("windows", target(arch)[0]).binary, directory / "libcronet.dll")
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


def check(directory: Path, arch: str = "x64") -> int:
    """Every required file is there, nothing is a link, and every executable, DLL and
    extension module matches the target. Returns how many of those there are."""
    expected = target(arch)[1]
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
        if machine != expected:
            shown = "not a PE file" if machine is None else f"machine {machine:#06x}"
            raise BuildError(f"{name} is {shown}, not {arch}")
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
    """NSIS calls that remove exactly the files and directories in ``directory`` from
    $INSTDIR, contents before their directories; the uninstaller never removes a directory
    recursively, so files it did not install stay."""
    lines = []
    for path in _deepest_first(directory):
        relative = nsis_string(str(path.relative_to(directory)).replace("/", "\\"))
        if path.is_dir():
            lines.append(f'RMDir "$INSTDIR\\{relative}"')
        else:
            lines.append(f'!insertmacro RemoveInstalledFile "$INSTDIR\\{relative}"')
    return "\n".join(lines) + "\n"


def sign(*arguments: str | Path) -> None:
    """package/windows/sign.ps1 with ``arguments``: ``"-Directory", path`` or files."""
    build_apple._run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            SIGN_SCRIPT,
            *arguments,
        ]
    )


def find_makensis(given: str | None) -> Path:
    candidates = [given] if given else ["makensis"]
    if not given and (programs := os.environ.get("PROGRAMFILES(X86)")):
        candidates.append(str(Path(programs) / "NSIS" / "makensis.exe"))
    for candidate in candidates:
        if found := shutil.which(candidate):
            return Path(found)
    raise BuildError("makensis (NSIS 3) is missing; install NSIS or pass --makensis")


def build_installer(
    directory: Path,
    version: str,
    makensis: Path,
    output: Path,
    arch: str = "x64",
    signed: bool = False,
) -> Path:
    """Compile installer.nsi against ``directory`` into ``output``/<installer name>. The
    definitions and the uninstall list go into ``output``/nsis. ``signed`` signs the setup
    UI's plugin, the uninstaller and the installer; ``directory`` is already signed."""
    work = output / "nsis"
    work.mkdir(parents=True, exist_ok=True)
    plugin = build_installer_ui.build(work / "ui")
    if signed:
        sign(plugin)
    files = work / "uninstall-files.nsh"
    # makensis reads a file without a byte order mark in the system's code page, unless an
    # !include names the charset: the generated files get the mark, installer.nsi the option.
    files.write_text(uninstall_list(directory), encoding="utf-8-sig")
    installer = output / installer_name(version, arch)
    installer.unlink(missing_ok=True)
    definitions = {
        "APP_DIRECTORY": directory,
        "APP_VERSION": version,
        "APP_ARCH": arch,
        "OUTPUT_FILE": installer,
        "SETUP_SCRIPT": SETUP_SCRIPT,
        "APP_ICON": ICON,
        "UNINSTALL_FILES": files,
        "REMOVE_FILES_INCLUDE": PACKAGE / "remove-files.nsh",
        "INSTALLER_UI_DIR": plugin.parent,
        "INSTALLER_UI_INCLUDE": PACKAGE / "ui" / "interface.nsh",
        "INSTALLER_FONTS": ROOT / "src" / "solaris" / "ui" / "fonts",
        "INSTALLER_LICENSE": ROOT / "LICENSE",
    }
    if signed:
        definitions["SIGN_SCRIPT"] = SIGN_SCRIPT
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
    parser.add_argument(
        "--arch", choices=tuple(TARGETS), help="target architecture (default: native Python)"
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="a signed release (D42): link the service with SignedRelease, and sign the "
        "programs and the installer when compiling it; needs the release workflow's signing setup",
    )
    arguments = parser.parse_args()
    if sys.platform != "win32":
        sys.exit("build_windows: the Windows installer is built on Windows")
    version = build_apple._version()
    try:
        arch = arguments.arch or python_architecture()
        if not arguments.installer_only and python_architecture() != arch:
            raise BuildError(f"use native {arch} Python to build the {arch} package")
        build = build_directory(arch)
        directory = build / APP_NAME
        makensis = None if arguments.skip_installer else find_makensis(arguments.makensis)
        if not arguments.installer_only:
            _check_nuitka()
            geodata = fetch_geodata.fetch()
            service = build_service(version, arch, arguments.sign)
            ui = build_ui(version, arch)
            directory = assemble(ui, service, geodata, arch)
            symbol_dir = build / "symbols"
            build_apple._remove(symbol_dir)
            symbols.windows(directory / CLIENT, directory / SERVICE, build / "nuitka", symbol_dir)
            for pdb in directory.rglob("*.pdb"):
                pdb.unlink()  # retained separately, not part of the installer
        count = check(directory, arch)
        size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        shown = directory.relative_to(ROOT)
        print(f"build_windows: {shown}, {count} {arch} binaries, {size / 2**20:.1f} MiB")
        if makensis is not None:
            if arguments.sign:
                sign("-Directory", directory)
            installer = build_installer(directory, version, makensis, build, arch, arguments.sign)
            megabytes = installer.stat().st_size / 2**20
            signed = "signed" if arguments.sign else "unsigned"
            print(f"build_windows: {installer.relative_to(ROOT)}, {megabytes:.1f} MiB ({signed})")
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"build_windows: {error}")


if __name__ == "__main__":
    main()
