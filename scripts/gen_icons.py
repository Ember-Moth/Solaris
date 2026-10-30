"""Generate Solaris's icons from one geometry: the corona mark (DESIGN.md "Shapes").

    uv run python scripts/gen_icons.py

Writes the app icons (src/solaris/ui/brand/*.svg, package/icons/solaris.ico and, on macOS
with iconutil, package/icons/solaris.icns) and the six tray SVGs in src/solaris/ui/tray/.
The mark: a solid core and a ring with a gap at the upper right. The ring runs clockwise
from -40 degrees to 250 degrees, leaving 70 degrees open; angles as in SVG, y down.
Everything is committed, so a build needs no renderer; run this after changing the
geometry, and check the result by eye.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "src" / "solaris" / "ui" / "brand"
TRAY = ROOT / "src" / "solaris" / "ui" / "tray"
PACKAGE = ROOT / "package" / "icons"

GRAPHITE = "#15171C"  # DESIGN.md light `text`
BLUE = "#1F5EFF"  # DESIGN.md light `primary`
WHITE = "#FFFFFF"
GAP_START, GAP_END = -40.0, 250.0  # the ring's ends; the gap is between them, at the top right


def ring_path(cx: float, cy: float, r: float) -> str:
    """The ring as one open arc, drawn clockwise from GAP_START to GAP_END."""

    def point(degrees: float) -> str:
        a = math.radians(degrees)
        return f"{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}"

    return f"M{point(GAP_START)}A{r:g} {r:g} 0 1 1 {point(GAP_END)}"


def tile_svg(size: int, inset: float, small: bool) -> str:
    """An app icon: the mark, white ring and blue core, on a graphite rounded square.
    ``inset`` is the transparent margin as a share of the canvas (macOS draws its icons
    on 824 of 1024 units); ``small`` thickens the ring and core for 32 px and below."""
    body = size * (1 - 2 * inset)
    x = size * inset
    c = size / 2
    ring_r, stroke, core = (0.27, 0.10, 0.16) if small else (0.29, 0.06, 0.15)
    rounded = body * 0.225
    edge = (
        ""
        if small
        else f'<rect x="{x + 1:g}" y="{x + 1:g}" width="{body - 2:g}" height="{body - 2:g}" '
        f'rx="{rounded - 1:g}" fill="none" stroke="{WHITE}" stroke-opacity="0.08" '
        'stroke-width="2"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f'<rect x="{x:g}" y="{x:g}" width="{body:g}" height="{body:g}" rx="{rounded:g}" '
        f'fill="{GRAPHITE}"/>'
        f"{edge}"
        f'<circle cx="{c:g}" cy="{c:g}" r="{body * core:g}" fill="{BLUE}"/>'
        f'<path d="{ring_path(c, c, body * ring_r)}" fill="none" stroke="{WHITE}" '
        f'stroke-width="{body * stroke:g}" stroke-linecap="round"/>'
        "</svg>\n"
    )


def tray_svg(color: str, opacity: float) -> str:
    """The mark in one colour, as DESIGN.md's tray rules ask: 32 px, on a 20-unit view."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="2 2 20 20">\n'
        f'  <g fill="none" stroke="{color}" stroke-opacity="{opacity:g}" stroke-width="2.4" '
        'stroke-linecap="round">\n'
        f'    <circle cx="12" cy="12" r="3.6" fill="{color}" fill-opacity="{opacity:g}" '
        'stroke="none"/>\n'
        f'    <path d="{ring_path(12, 12, 8)}"/>\n'
        "  </g>\n"
        "</svg>\n"
    )


def render(svg: str, size: int) -> bytes:
    """PNG bytes of ``svg`` at ``size`` pixels, through Qt's SVG renderer."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QImageWriter, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not QImageWriter(buffer, QByteArray(b"png")).write(image):
        raise RuntimeError(f"cannot encode a {size} px PNG")
    return bytes(buffer.data().data())


def ico(images: dict[int, bytes]) -> bytes:
    """A Windows icon holding one PNG per size (PNG entries work since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, data = b"", b""
    offset = len(header) + 16 * len(images)
    for size, png in sorted(images.items()):
        side = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    BRAND.mkdir(parents=True, exist_ok=True)
    PACKAGE.mkdir(parents=True, exist_ok=True)

    # macOS: 824 of 1024 units, as Apple's icon grid; Windows and the window icon: nearly
    # full bleed, since Windows draws no shape of its own around an icon.
    macos = tile_svg(1024, 100 / 1024, small=False)
    windows = tile_svg(256, 8 / 256, small=False)
    (BRAND / "app-icon-macos.svg").write_text(macos)
    (BRAND / "app-icon.svg").write_text(windows)

    def windows_tile(size: int) -> str:
        return windows if size > 32 else tile_svg(size, 0.03, small=True)

    (PACKAGE / "solaris.ico").write_bytes(
        ico({s: render(windows_tile(s), s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)})
    )

    for variant, color in (
        ("template", "#000000"),
        ("light-taskbar", GRAPHITE),
        ("dark-taskbar", WHITE),
    ):
        for state, opacity in (("on", 1.0), ("off", 0.45)):
            (TRAY / f"tray-{variant}-{state}.svg").write_text(tray_svg(color, opacity))

    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("iconutil is macOS only; solaris.icns left as it is")
        return
    with tempfile.TemporaryDirectory() as temp:
        iconset = Path(temp) / "solaris.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = points * scale
                svg = macos if pixels > 32 else tile_svg(pixels, 100 / 1024, small=True)
                name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
                (iconset / name).write_bytes(render(svg, pixels))
        subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(PACKAGE / "solaris.icns")], check=True
        )
    print("wrote", ", ".join(str(p.relative_to(ROOT)) for p in (BRAND, PACKAGE, TRAY)))


if __name__ == "__main__":
    main()
