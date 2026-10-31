"""Make the macOS disk image (docs/release.md §1, apple/README.md) from a built Solaris.app.

    uv run --no-project --with dmgbuild==1.6.7 python scripts/build_dmg.py \\
        build/app/Solaris.app build/release/Solaris-<version>-macos-arm64.dmg

The mounted volume opens as one fixed Finder window without toolbar or sidebar: Solaris.app on
the left and a shortcut to /Applications on the right, large icons on a plain background with
an arrow between them (package/dmg, drawn by scripts/gen_dmg_background.py from the layout
here), and the app's icon as the volume's. dmgbuild writes the window's .DS_Store itself rather
than scripting Finder, so this runs on CI runners and on an unsigned build alike. The image is
HFS+ with LZMA compression (ULMO, macOS 10.15 and later), shrunk to its contents.

Then the image is checked: hdiutil verifies its checksums, and attached read-only, without
showing it in Finder, it holds the app with a valid signature, the shortcut, the background
and the volume icon. Nothing here signs or notarizes; .github/workflows/release.yml does.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DMGBUILD_VERSION = "1.6.7"

VOLUME_NAME = "Solaris"
APP_NAME = "Solaris.app"
APPLICATIONS = "Applications"
# The window in points, and where it opens. Finder's title bar takes about 30 points of the
# height; the background is drawn to the whole size, so its bottom edge is what that hides.
WINDOW_SIZE = (600, 360)
WINDOW_ORIGIN = (200, 160)
ICON_SIZE = 128
TEXT_SIZE = 13
# The icons' centres, in points from the top left below the title bar: icon and label sit in
# the middle of what shows, and the drag goes left to right.
ICON_POSITIONS = {APP_NAME: (160, 155), APPLICATIONS: (440, 155)}
BACKGROUND = ROOT / "package" / "dmg" / "background.png"  # dmgbuild adds background@2x.png
VOLUME_ICON = ROOT / "package" / "icons" / "solaris.icns"  # scripts/gen_icons.py
FORMAT = "ULMO"
# What the attached image must hold besides the app and the shortcut.
HIDDEN = (".background.tiff", ".VolumeIcon.icns", ".DS_Store")
DETACH_ATTEMPTS = 5


class DiskImageError(Exception):
    pass


def _run(command: list[str | Path], **options: Any) -> subprocess.CompletedProcess[str]:
    shown = [
        part.relative_to(ROOT) if isinstance(part, Path) and part.is_relative_to(ROOT) else part
        for part in command
    ]
    print("+", " ".join(map(str, shown)), flush=True)
    return subprocess.run([str(part) for part in command], check=True, text=True, **options)


def settings(app: Path) -> dict[str, Any]:
    """dmgbuild's settings for an image of ``app``."""
    width, height = WINDOW_SIZE
    return {
        "format": FORMAT,
        "filesystem": "HFS+",
        "files": [(str(app), APP_NAME)],
        "symlinks": {APPLICATIONS: "/Applications"},
        # No hide_extensions: SetFile's Finder information on the bundle breaks its signature.
        "icon": str(VOLUME_ICON),
        "background": str(BACKGROUND),
        "window_rect": (WINDOW_ORIGIN, (width, height)),
        "default_view": "icon-view",
        "show_toolbar": False,
        "show_sidebar": False,
        "show_status_bar": False,
        "show_pathbar": False,
        "show_tab_view": False,
        "show_icon_preview": False,
        "include_list_view_settings": False,
        "arrange_by": None,
        "icon_size": ICON_SIZE,
        "text_size": TEXT_SIZE,
        "label_pos": "bottom",
        "icon_locations": ICON_POSITIONS,
    }


def build(app: Path, image: Path) -> None:
    image.parent.mkdir(parents=True, exist_ok=True)
    image.unlink(missing_ok=True)
    dmgbuild = importlib.import_module("dmgbuild")
    print(f"+ dmgbuild {image.name}", flush=True)
    dmgbuild.build_dmg(str(image), VOLUME_NAME, settings=settings(app))


def _detach(mount: Path) -> None:
    """Spotlight can hold a fresh volume for a moment; try again before forcing it."""
    for _ in range(DETACH_ATTEMPTS):
        if subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True).returncode == 0:
            return
        time.sleep(1)
    _run(["hdiutil", "detach", "-force", mount])


def check(image: Path) -> None:
    _run(["hdiutil", "verify", "-quiet", image])
    with tempfile.TemporaryDirectory(prefix="solaris-dmg-") as temporary:
        mount = Path(temporary) / "volume"
        mount.mkdir()
        attach = ["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen"]
        _run([*attach, "-mountpoint", mount, image], stdout=subprocess.DEVNULL)
        try:
            app = mount / APP_NAME
            if not app.is_dir():
                raise DiskImageError(f"{image.name} holds no {APP_NAME}")
            _run(["codesign", "--verify", "--deep", "--strict", app])
            link = mount / APPLICATIONS
            if not link.is_symlink() or link.readlink() != Path("/Applications"):
                raise DiskImageError(f"{image.name} has no shortcut to /Applications")
            for name in HIDDEN:
                if not (mount / name).is_file():
                    raise DiskImageError(f"{image.name} has no {name}")
            flag = subprocess.run(
                ["GetFileInfo", "-aC", str(mount)], check=True, capture_output=True, text=True
            )
            if flag.stdout.strip() != "1":
                raise DiskImageError(f"{image.name} does not show its volume icon")
        finally:
            _detach(mount)


def main() -> None:
    parser = argparse.ArgumentParser(description="Make the macOS disk image.")
    parser.add_argument("app", type=Path, help="the built Solaris.app")
    parser.add_argument("image", type=Path, help="the disk image to write")
    arguments = parser.parse_args()
    if sys.platform != "darwin":
        sys.exit("build_dmg: disk images are made on macOS")
    try:
        installed = importlib.metadata.version("dmgbuild")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != DMGBUILD_VERSION:
        version = DMGBUILD_VERSION
        sys.exit(f"build_dmg: run with uv run --with dmgbuild=={version} (found {installed})")
    app: Path = arguments.app
    image: Path = arguments.image
    if app.name != APP_NAME or not app.is_dir():
        sys.exit(f"build_dmg: {app} is not a built {APP_NAME}")
    try:
        _run(["codesign", "--verify", "--deep", "--strict", app])
        build(app, image)
        check(image)
    except (DiskImageError, subprocess.CalledProcessError) as error:
        sys.exit(f"build_dmg: {error}")
    print(f"build_dmg: {image}, {image.stat().st_size / 2**20:.1f} MiB ({FORMAT})")


if __name__ == "__main__":
    main()
