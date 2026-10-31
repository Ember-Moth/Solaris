"""scripts/build_dmg.py's window and the background drawn for it. The image itself is made on
macOS by the release workflow and by CI's apple job, which needs dmgbuild."""

from __future__ import annotations

import struct
from pathlib import Path

from scripts import build_dmg


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
