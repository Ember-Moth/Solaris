"""scripts/gen_screenshots.py renders the documentation's screenshots from the real window."""

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from scripts import gen_screenshots


def test_renders_a_shot_offscreen(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The routing page switches to my plan and its groups' tab, which is most of what a
    shot can ask for; the picture is the window in its frame, not an empty one."""
    monkeypatch.setattr(gen_screenshots, "WARM_UP_POLLS", 1)
    monkeypatch.setattr(gen_screenshots, "SETTLE_MS", 300)
    (path,) = gen_screenshots.capture(tmp_path, "dark", ["routing-groups"])
    assert path == tmp_path / "routing-groups-dark.png"
    image = QImage(str(path))
    width, height = gen_screenshots.SIZE
    assert image.width() >= width
    assert image.width() * height == image.height() * width
    assert image.pixelColor(0, 0).alpha() == 0  # outside the rounded corner
    middle = image.height() // 2
    row = {image.pixelColor(x, middle).name() for x in range(0, image.width(), 4)}
    assert len(row) > 8, row  # the sidebar, the page and the text on them
