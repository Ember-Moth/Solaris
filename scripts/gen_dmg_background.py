"""Draw the disk image's background: package/dmg/background.png and background@2x.png.

    uv run python scripts/gen_dmg_background.py

A plain neutral fill the size of the window in scripts/build_dmg.py, and a thin arrow from
where Solaris.app sits to where the Applications shortcut sits; no text (DESIGN.md: neutral,
no marketing). Finder draws the icons' labels dark in light mode and white in dark mode on
the same picture, so the fill is the mid grey that gives both about 4.6:1. dmgbuild combines
the two files into one HiDPI TIFF. Both are committed, so a build needs no renderer; run this
after changing the layout in build_dmg.py, and check the result by eye.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as a script, Python puts scripts/ on the path rather than the root.
sys.path.insert(0, str(ROOT))

from scripts.build_dmg import (  # noqa: E402
    APP_NAME,
    APPLICATIONS,
    BACKGROUND,
    ICON_POSITIONS,
    ICON_SIZE,
    WINDOW_SIZE,
)

FILL = "#72767E"  # 4.6:1 against black and against white
ARROW = "#E6E9ED"  # DESIGN.md dark `text`
STROKE = 2.0  # points
GAP = 24.0  # between an icon's square and the arrow, in points
HEAD = 9.0  # the arrowhead's arms, along each axis
INCHES_PER_METRE = 39.3701


def draw(scale: int) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    width, height = WINDOW_SIZE
    image = QImage(width * scale, height * scale, QImage.Format.Format_RGB32)
    image.fill(QColor(FILL))
    # 72 dpi a point: the @2x file has twice the pixels in the same size.
    dots = round(72 * scale * INCHES_PER_METRE)
    image.setDotsPerMeterX(dots)
    image.setDotsPerMeterY(dots)

    (app_x, y), (applications_x, _) = ICON_POSITIONS[APP_NAME], ICON_POSITIONS[APPLICATIONS]
    start = app_x + ICON_SIZE / 2 + GAP
    end = applications_x - ICON_SIZE / 2 - GAP
    arrow = QPainterPath(QPointF(start, y))
    arrow.lineTo(end, y)
    arrow.moveTo(end - HEAD, y - HEAD)
    arrow.lineTo(end, y)
    arrow.lineTo(end - HEAD, y + HEAD)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    pen = QPen(QColor(ARROW), STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPath(arrow)
    painter.end()

    path = BACKGROUND if scale == 1 else BACKGROUND.with_stem(f"{BACKGROUND.stem}@{scale}x")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path)):  # PNG, by the suffix
        raise RuntimeError(f"cannot write {path}")
    print("wrote", path.relative_to(ROOT))


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])
    for scale in (1, 2):
        draw(scale)


if __name__ == "__main__":
    main()
