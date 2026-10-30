"""Assemble Solaris.app (docs/architecture.md §6.1, apple/README.md): the UI built by Nuitka in
Contents/MacOS, SolarisManager.dylib in Contents/Frameworks and the system extension in
Contents/Library/SystemExtensions, then sign it with the Developer ID identity and verify.

    uv run --with nuitka==4.2.2 python scripts/build_app.py               # signed
    uv run --with nuitka==4.2.2 python scripts/build_app.py --unsigned    # ad hoc

The native parts come from scripts/build_apple.py's steps, built for the same version and build
number; the app lands in build/app/Solaris.app. Signing follows build_apple.py: the identity is
the team's Developer ID Application certificate that both profiles authorize, Xcode has signed
the dylib and the extension, and this script signs the UI's code from the inside out, then the
app with apple/App/Solaris.entitlements and the Solaris Developer ID profile. Nothing here
installs, runs or notarizes the app: .github/workflows/release.yml notarizes, and the extension
creates a utun, so it only runs on a separate test Mac.

With SOLARIS_GOOGLE_CLIENT_ID and SOLARIS_GOOGLE_CLIENT_SECRET set, the app carries that Google
OAuth client and so Google Drive backup (google_client, docs/storage.md §4); without them it has
none. scripts/build_windows.py does the same.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Run as a script, Python puts scripts/ on the path rather than the root.
sys.path.insert(0, str(ROOT))

from scripts import build_apple  # noqa: E402
from scripts.build_apple import BuildError, Profile  # noqa: E402
from solaris.models.backup_google import (  # noqa: E402
    CLIENT_FILE_NAME,
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    configured_client,
)

BUILD = ROOT / "build" / "app"
ENTRY = ROOT / "package" / "entry.py"
CORE = ROOT / "src" / "core"

APP_NAME = "Solaris"
# Not "Solaris": the volume is case-insensitive, and Contents/MacOS/solaris holds the package's
# data (ui/, translations/) where the compiled modules look for it.
EXECUTABLE = "SolarisUI"
URL_SCHEMES = ("solaris", "clash")  # one-click import (D6); the UI handles the links
CORE_VERSION_KEY = "SolarisCoreVersion"  # read by solaris.host.extension.bundled_host
MIHOMO_MODULE = "github.com/metacubex/mihomo"
NUITKA_VERSION = "4.2.2"

# Qt modules that --include-qt-plugins=qml brings and the UI never loads. Libraries are named
# QtX in the bundle. Kept: QtDBus (QtGui links it), QtOpenGL (the QtQuick binding needs it),
# QtWidgets (Qt.labs.platform links it), QtSvg (the icons), QtQuickShapesDesignHelpers (Shapes
# links it), QtQuickDialogs2* (FileDialog), and the macOS and native styles, which the dialogs'
# Qt Quick fallback would use.
UNUSED_QT_LIBRARIES = (
    "3D",
    "Charts",
    "Concurrent",
    "DataVisualization",
    "Graphs",
    "LabsAnimation",
    "LabsFolderListModel",
    "LabsSettings",
    "LabsSharedImage",
    "LabsStyleKit",
    "LabsSynchronizer",
    "LabsWavefrontMesh",
    "Location",
    "Multimedia",
    "OpenGLWidgets",
    "Pdf",
    "Positioning",
    "QmlDesignSupport",
    "QmlLocalStorage",
    "QmlXmlListModel",
    "Quick3D",
    "QuickControls2FluentWinUI3",
    "QuickControls2Fusion",
    "QuickControls2IOS",
    "QuickControls2Imagine",
    "QuickControls2Material",
    "QuickControls2Universal",
    "QuickEffects",
    "QuickParticles",
    "QuickTest",
    "QuickTimeline",
    "QuickVectorImage",
    "RemoteObjects",
    "Scxml",
    "Sensors",
    "ShaderTools",
    "SpatialAudio",
    "Sql",
    "StateMachine",
    "Test",
    "TextToSpeech",
    "VirtualKeyboard",
    "WebChannel",
    "WebEngine",
    "WebSockets",
    "WebView",
)
# The QML modules of the same families, under PySide6/qml.
UNUSED_QML_DIRECTORIES = (
    "Qt/labs/StyleKit",
    "Qt/labs/animation",
    "Qt/labs/folderlistmodel",
    "Qt/labs/settings",
    "Qt/labs/sharedimage",
    "Qt/labs/synchronizer",
    "Qt/labs/wavefrontmesh",
    "Qt3D",
    "Qt5Compat",
    "QtCharts",
    "QtDataVisualization",
    "QtGraphs",
    "QtLocation",
    "QtMultimedia",
    "QtPositioning",
    "QtQml/StateMachine",
    "QtQml/XmlListModel",
    "QtQuick/Controls/FluentWinUI3",
    "QtQuick/Controls/Fusion",
    "QtQuick/Controls/Imagine",
    "QtQuick/Controls/Material",
    "QtQuick/Controls/Universal",
    "QtQuick/Controls/designer",
    "QtQuick/Controls/iOS",
    "QtQuick/Effects",
    "QtQuick/LocalStorage",
    "QtQuick/Particles",
    "QtQuick/Pdf",
    "QtQuick/Scene2D",
    "QtQuick/Scene3D",
    "QtQuick/Timeline",
    "QtQuick/VectorImage",
    "QtQuick/VirtualKeyboard",
    "QtQuick3D",
    "QtRemoteObjects",
    "QtScxml",
    "QtSensors",
    "QtTest",
    "QtTextToSpeech",
    "QtWebChannel",
    "QtWebEngine",
    "QtWebSockets",
    "QtWebView",
)
# The UI shows SVG; PNG is built in.
UNUSED_IMAGE_FORMATS = ("gif", "icns", "ico", "jpeg", "macheif", "macjp2", "pdf", "tga", "tiff")
EXCLUDED = (
    *(f"*Qt{name}*" for name in UNUSED_QT_LIBRARIES),
    *(f"*/qml/{directory}/*" for directory in UNUSED_QML_DIRECTORIES),
    *(f"*/imageformats/libq{name}.dylib" for name in UNUSED_IMAGE_FORMATS),
    # Qt's own TLS is Secure Transport; the OpenSSL backend would bring whatever OpenSSL the
    # build machine has. Python's ssl module carries its own.
    "*qopensslbackend*",
    "libssl*.dylib",
    "libcrypto*.dylib",
)
MACHO_MAGIC = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def _run(command: list[str | Path], **options: Any) -> None:
    build_apple._run(command, **options)


def _output(command: list[str | Path], environment: dict[str, str] | None = None) -> str:
    arguments = [str(part) for part in command]
    completed = subprocess.run(
        arguments, check=True, capture_output=True, text=True, env=environment
    )
    return completed.stdout


def core_version() -> str:
    """mihomo's version as the core reports it (engine.moduleVersion), from go.mod."""
    environment = os.environ | {"GOPROXY": build_apple.GOPROXY}
    template = "{{if .Replace}}{{.Replace.Version}}{{else}}{{.Version}}{{end}}"
    command: list[str | Path] = ["go", "-C", CORE, "list", "-m", "-f", template, MIHOMO_MODULE]
    return _output(command, environment).strip()


def google_client(directory: Path) -> list[str]:
    """The Nuitka options that bundle the Google OAuth client the environment names, written
    to ``directory`` and read by solaris.models.backup_google beside the package. Without one
    the build has no Google Drive backup; half of one stops the build."""
    if not any(os.environ.get(name, "").strip() for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV)):
        print("build: no Google OAuth client in the environment, so no Google Drive backup")
        return []
    client = configured_client(os.environ, None)
    if client is None:
        raise BuildError(f"set both {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV}, or neither")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CLIENT_FILE_NAME
    path.write_text(
        json.dumps({"client_id": client.id, "client_secret": client.secret}), encoding="utf-8"
    )
    return [f"--include-data-files={path}=solaris/{CLIENT_FILE_NAME}"]


def build_ui(version: str) -> Path:
    """The Nuitka app bundle of package/entry.py; its own options are in that file."""
    output = BUILD / "nuitka"
    if output.exists():
        shutil.rmtree(output)
    clang = Path(_output(["xcrun", "--find", "clang"]).strip())
    environment = os.environ | {
        # Apple's clang, ahead of any other on PATH.
        "CC": str(clang),
        "PATH": f"{clang.parent}{os.pathsep}{os.environ['PATH']}",
        "SDKROOT": _output(["xcrun", "--show-sdk-path"]).strip(),
        "MACOSX_DEPLOYMENT_TARGET": build_apple.DEPLOYMENT_TARGET,
    }
    command: list[str | Path] = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=app",
        "--enable-plugins=pyside6",
        "--include-qt-plugins=qml",
        # The Nuitka plugin pulls in the QtWidgets binding for QtQuick; the UI never imports it.
        "--nofollow-import-to=PySide6.QtWidgets",
        "--include-distribution-metadata=solaris",
        f"--macos-app-name={APP_NAME}",
        f"--macos-signed-app-name={build_apple.APP_ID}",
        f"--macos-app-version={version}",
        f"--macos-app-macos-min-version={build_apple.DEPLOYMENT_TARGET}",
        f"--macos-app-icon={ROOT / 'package' / 'icons' / 'solaris.icns'}",
        f"--output-filename={EXECUTABLE}",
        f"--output-dir={output}",
        # ccache, when installed, must find the compiler under its bare name; skip it.
        "--disable-ccache",
        "--assume-yes-for-downloads",
        *(f"--noinclude-dlls={pattern}" for pattern in EXCLUDED),
        *(f"--noinclude-data-files={pattern}" for pattern in EXCLUDED),
        *google_client(BUILD),
        ENTRY,
    ]
    _run(command, env=environment)
    return output / f"{ENTRY.stem}.app"


def assemble(ui: Path, dylib: Path, extension: Path, version: str, core: str, minimum: str) -> Path:
    """Solaris.app from the Nuitka bundle, the manager and the extension. The app takes the
    extension's build number, and ``minimum`` as the oldest macOS it opens on."""
    app = BUILD / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    _run(["ditto", ui, app])
    contents = app / "Contents"
    _run(["ditto", dylib, contents / "Frameworks" / dylib.name])
    _run(["ditto", extension, contents / "Library" / "SystemExtensions" / extension.name])
    tunnel = plistlib.loads((extension / "Contents" / "Info.plist").read_bytes())
    path = contents / "Info.plist"
    info = plistlib.loads(path.read_bytes())
    info.update(
        {
            "CFBundleIdentifier": build_apple.APP_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleExecutable": EXECUTABLE,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": tunnel["CFBundleVersion"],
            "LSMinimumSystemVersion": minimum,
            "CFBundleURLTypes": [
                {"CFBundleURLName": build_apple.APP_ID, "CFBundleURLSchemes": [*URL_SCHEMES]}
            ],
            CORE_VERSION_KEY: core,
        }
    )
    path.write_bytes(plistlib.dumps(info))
    return app


def _machos(directory: Path) -> Iterator[Path]:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as file:
                if file.read(4) in MACHO_MAGIC:
                    yield path


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(map(int, version.split(".")))


def _outside(path: str) -> bool:
    """An absolute path outside the system, such as the build machine's Python."""
    return path.startswith("/") and not path.startswith(("/usr/lib/", "/System/"))


def check_ui(ui: Path) -> str:
    """Every Mach-O is arm64 alone and loads nothing outside the bundle and the system.
    Install names and rpaths that still point into the build machine become @rpath names or
    go. Returns the oldest macOS all of them run on: the deployment target, unless a
    dependency was built for a newer one (PySide6's wheels tag an older macOS than their
    binaries need)."""
    minimum = build_apple.DEPLOYMENT_TARGET
    newer: dict[str, list[str]] = {}
    count = 0
    for path in _machos(ui / "Contents"):
        count += 1
        name = str(path.relative_to(ui / "Contents" / "MacOS"))
        architectures = _output(["lipo", "-archs", path]).split()
        if architectures != [build_apple.ARCH]:
            raise BuildError(f"{name} is built for {architectures}, not {build_apple.ARCH}")
        relocation: list[str | Path] = []
        for block in _output(["otool", "-l", path]).split("Load command ")[1:]:
            command = re.search(r"^\s*cmd (\w+)$", block, re.MULTILINE)
            value = re.search(r"^\s*(?:name|path) (\S+) \(offset", block, re.MULTILINE)
            version = re.search(r"^\s*minos (\S+)$", block, re.MULTILINE)
            if version and _version_key(version[1]) > _version_key(build_apple.DEPLOYMENT_TARGET):
                newer.setdefault(version[1], []).append(name)
                minimum = max(minimum, version[1], key=_version_key)
            if command is None or value is None or not _outside(value[1]):
                continue
            if command[1] == "LC_ID_DYLIB":
                relocation += ["-id", f"@rpath/{path.name}"]
            elif command[1] == "LC_RPATH":
                relocation += ["-delete_rpath", value[1]]
            else:
                raise BuildError(f"{name} loads {value[1]}")
        if relocation:
            _run(["install_name_tool", *relocation, path], stderr=subprocess.DEVNULL)
    print(f"{count} Mach-O files in the UI: {build_apple.ARCH}, no outside libraries")
    for version, names in sorted(newer.items(), key=lambda item: _version_key(item[0])):
        shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        print(f"warning: macOS {version} needed by {shown}")
    if minimum != build_apple.DEPLOYMENT_TARGET:
        print(f"warning: Solaris.app needs macOS {minimum}, above {build_apple.DEPLOYMENT_TARGET}")
    return minimum


def _chunks(paths: list[Path], size: int = 64) -> Iterator[list[Path]]:
    for start in range(0, len(paths), size):
        yield paths[start : start + size]


def sign(app: Path, identity: str, profile: Profile) -> None:
    """Inside out: the UI's own Mach-O files, then the app with its entitlements and profile.
    Xcode has signed the dylib and the extension; their signatures stay."""
    contents = app / "Contents"
    executable = contents / "MacOS" / EXECUTABLE
    nested = [path for path in _machos(contents / "MacOS") if path != executable]
    base: list[str | Path] = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--options",
        "runtime",
        "--timestamp",
    ]
    for chunk in _chunks(nested):
        _run([*base, *chunk], stdout=subprocess.DEVNULL)
    shutil.copyfile(profile.path, contents / "embedded.provisionprofile")
    _run([*base, "--entitlements", build_apple.APP_ENTITLEMENTS, app])


def sign_ad_hoc(app: Path) -> None:
    """For a machine without the certificate: enough to inspect the bundle, not to activate
    the extension."""
    _run(["codesign", "--force", "--deep", "--sign", "-", app])


def verify(app: Path, identity: str, app_profile: Profile, tunnel_profile: Profile) -> None:
    build_apple.verify(app, identity, app_profile, tunnel_profile)
    contents = app / "Contents"
    for path in _machos(contents / "MacOS"):
        details = subprocess.run(
            ["codesign", "--display", "--verbose=4", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stderr
        name = path.relative_to(app)
        if f"TeamIdentifier={build_apple.TEAM}" not in details.splitlines():
            raise BuildError(f"{name} is not signed by team {build_apple.TEAM}")
        if not re.search(r"flags=0x[0-9a-f]+\([^)]*runtime", details):
            raise BuildError(f"{name} is signed without the hardened runtime")
        if "Timestamp=" not in details:
            raise BuildError(f"{name} has no secure timestamp")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble and sign Solaris.app.")
    parser.add_argument(
        "--unsigned",
        action="store_true",
        help="build the native parts unsigned and sign the app ad hoc",
    )
    parser.add_argument(
        "--build-number",
        default=dt.datetime.now(dt.UTC).strftime("%Y%m%d.%H%M%S"),
        help="CFBundleVersion of the app and the extension (default: the UTC time)",
    )
    arguments = parser.parse_args()
    if sys.platform != "darwin":
        sys.exit("build_app: Solaris.app is built on macOS")
    try:
        installed = importlib.metadata.version("nuitka")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != NUITKA_VERSION:
        sys.exit(f"build_app: run with uv run --with nuitka=={NUITKA_VERSION} (found {installed})")
    if shutil.which("xcodegen") is None:
        sys.exit("build_app: XcodeGen is missing (brew install xcodegen)")
    version = build_apple._version()
    try:
        identity = None
        profiles: tuple[Profile, Profile] | None = None
        if not arguments.unsigned:
            profiles = (
                build_apple.find_profile(build_apple.APP_PROFILE),
                build_apple.find_profile(build_apple.TUNNEL_PROFILE),
            )
            identity = build_apple.find_identity(list(profiles))
        build_apple.fetch_geodata()
        build_apple.build_go(build_apple.GO_TAGS, version)
        products = build_apple.build_xcode(identity, version, arguments.build_number)
        dylib, extension = build_apple.collect(products)
        build_apple.check_products(dylib, extension)
        ui = build_ui(version)
        minimum = check_ui(ui)
        app = assemble(ui, dylib, extension, version, core_version(), minimum)
        if identity and profiles:
            sign(app, identity, profiles[0])
            verify(app, identity, *profiles)
        else:
            sign_ad_hoc(app)
    except (BuildError, subprocess.CalledProcessError) as error:
        sys.exit(f"build_app: {error}")
    size = sum(path.stat().st_size for path in app.rglob("*") if path.is_file())
    kind = "signed" if identity else "ad hoc signed"
    print(f"build_app: {kind} {app.relative_to(ROOT)}, {size / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
