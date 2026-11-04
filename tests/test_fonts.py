"""Font coverage and Unicode behavior on the actual Qt platform used by the build."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import (
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QInputMethodEvent,
    QKeySequence,
    QPainter,
    QTextLayout,
)
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from solaris.app import MAIN_QML
from solaris.fonts import BUNDLED, DIRECTORY, register_fonts
from solaris.viewmodels.text_editing import TextEditing
from tests.support.unicode import CLUSTERS, TEXT
from tests.support.window import find


@pytest.fixture(autouse=True)
def fonts(qapp: QCoreApplication) -> None:
    register_fonts()


def test_bundled_assets_match_their_pinned_sources() -> None:
    records = json.loads((DIRECTORY / "sources.json").read_text("utf-8"))["files"]
    files = {item["file"] for item in records}
    assert {p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob("*.ttf")} == {
        relative for relative, _ in BUNDLED
    }
    assert files == {
        p.relative_to(DIRECTORY).as_posix()
        for p in DIRECTORY.rglob("*")
        if p.suffix == ".ttf" or p.name == "OFL.txt"
    }
    for item in records:
        data = (DIRECTORY / item["file"]).read_bytes()
        assert len(data) == item["size"], item["file"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["file"]


def test_emoji_font_has_color_data_for_both_platform_engines() -> None:
    """Loading and shaping COLRv1 alone can pass while macOS 26 paints no color."""
    relative = next(path for path, family in BUNDLED if family == "Noto Color Emoji")
    data = (DIRECTORY / relative).read_bytes()
    count = struct.unpack_from(">H", data, 4)[0]
    tables = {data[12 + 16 * i : 16 + 16 * i] for i in range(count)}
    assert {b"COLR", b"CPAL", b"SVG "} <= tables


def shaped(text: str, family: str = "Inter", weight: int = 400) -> QTextLayout:
    font = QFont(family)
    font.setPixelSize(14)
    font.setWeight(QFont.Weight(weight))
    layout = QTextLayout(text, font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1000)
    layout.endLayout()
    return layout


@pytest.mark.parametrize("primary", ["Inter", "JetBrains Mono"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Solaris 123", None),
        ("中文繁體龘", "Noto Sans SC"),
        ("🂡", "Noto Sans Symbols 2"),
        ("🇯🇵", "Noto Color Emoji"),
        ("👩🏽‍💻", "Noto Color Emoji"),
        ("👨‍👩‍👧‍👦", "Noto Color Emoji"),
        ("✈️", "Noto Color Emoji"),
        ("1️⃣", "Noto Color Emoji"),
    ],
)
def test_glyphs_use_bundled_fonts(primary: str, text: str, expected: str | None) -> None:
    layout = shaped(text, primary)
    runs = layout.glyphRuns()
    assert runs
    assert {run.rawFont().familyName() for run in runs} == {expected or primary}
    for run in runs:
        assert 0 not in run.glyphIndexes(), text
    if expected == "Noto Color Emoji":
        # Whole sequences become one glyph; a flag must not turn into two letters.
        assert sum(len(run.glyphIndexes()) for run in runs) == 1


@pytest.mark.parametrize("weight", [400, 500, 600])
def test_chinese_weight_matches_latin_role(weight: int) -> None:
    runs = shaped("中文", weight=weight).glyphRuns()
    assert {run.rawFont().familyName() for run in runs} == {"Noto Sans SC"}
    assert {run.rawFont().weight() for run in runs} == {weight}


def test_text_presentation_is_not_forced_to_emoji() -> None:
    runs = shaped("✈︎").glyphRuns()
    assert runs
    assert all(run.rawFont().familyName() != "Noto Color Emoji" for run in runs)
    assert all(0 not in run.glyphIndexes() for run in runs)


@pytest.mark.parametrize("text", ["🌈", "🇯🇵", "👩🏽‍💻", "✈️", "1️⃣"])
def test_emoji_renders_in_color(text: str) -> None:
    image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        font = QFont("Inter")
        font.setPixelSize(48)
        painter.setFont(font)
        painter.drawText(10, 65, text)
    finally:
        painter.end()
    colors = {
        (color.red(), color.green(), color.blue(), color.alpha())
        for y in range(100)
        for x in range(100)
        for color in (image.pixelColor(x, y),)
    }
    assert sum(a > 0 and max(r, g, b) - min(r, g, b) > 40 for r, g, b, a in colors) > 20


def test_missing_resources_are_diagnosed_in_a_fresh_application(tmp_path: Path) -> None:
    # No dependence on this machine having installed fonts with identical family names.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from solaris import fonts
app = QGuiApplication([])
fonts.DIRECTORY = Path(sys.argv[1])
fonts.register_fonts()
print('ready')
""",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert result.stdout.strip() == "ready"
    assert result.stderr.count("Cannot load bundled font") == len(BUNDLED)


def test_registering_for_another_engine_does_not_reload_fonts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(path: str) -> int:
        pytest.fail(f"font loaded twice: {path}")

    monkeypatch.setattr(QFontDatabase, "addApplicationFont", unexpected)
    register_fonts()


@pytest.mark.parametrize(
    "mode", [Qt.TextElideMode.ElideLeft, Qt.TextElideMode.ElideMiddle, Qt.TextElideMode.ElideRight]
)
def test_elision_keeps_whole_graphemes(mode: Qt.TextElideMode) -> None:
    metrics = QFontMetricsF(shaped(TEXT).font())
    parts = []
    for cluster in CLUSTERS:
        if parts:
            parts += [" ", "/", " "]
        parts.append(cluster)
    valid = {
        "".join(parts[:start]) + "…" + "".join(parts[end:])
        for start in range(len(parts) + 1)
        for end in range(start, len(parts) + 1)
    }
    for width in range(1, int(metrics.horizontalAdvance(TEXT)) + 2):
        text = metrics.elidedText(TEXT, mode, width)
        assert text in valid | {TEXT, ""}, repr(text)


@pytest.fixture
def editors(qtbot: QtBot) -> Iterator[tuple[QQuickWindow, QQuickItem, QQuickItem]]:
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(
        b"""import QtQuick
import QtQuick.Controls.Basic
import "components"
ApplicationWindow {
    visible: true
    width: 500; height: 360
    AppTextField {
        objectName: "field"; width: 400; y: 20
        property int edits: 0
        onTextEdited: edits += 1
    }
    CodeEditor { objectName: "editor"; width: 400; height: 220; y: 80 }
}""",
        QUrl.fromLocalFile(str(MAIN_QML)),
    )
    window = component.create()
    assert isinstance(window, QQuickWindow), [e.toString() for e in component.errors()]
    field = window.findChild(QQuickItem, "field")
    editor = window.findChild(QQuickItem, "editor")
    assert field is not None
    assert editor is not None
    try:
        window.requestActivate()
        qtbot.waitUntil(window.isActive)
        yield window, field, editor
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("which", ["field", "editor"])
def test_editing_and_clipboard_keep_unicode(
    qtbot: QtBot, editors: tuple[QQuickWindow, QQuickItem, QQuickItem], which: str
) -> None:
    window, field, editor = editors
    item = field if which == "field" else editor
    item.setProperty("text", TEXT)
    if which == "field":
        field.forceActiveFocus()
    else:
        area = find(editor, lambda i: i.property("cursorPosition") is not None)
        assert area is not None
        area.forceActiveFocus()
    focused = window.activeFocusItem()
    assert focused is not None
    focused.setProperty("cursorPosition", len(TEXT.encode("utf-16-le")) // 2)
    clipboard = QGuiApplication.clipboard()
    original = clipboard.text()
    try:
        QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.SelectAll))
        QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.Copy))
        qtbot.waitUntil(lambda: clipboard.text() == TEXT)
        QTest.keyClick(window, Qt.Key.Key_Backspace)
        assert item.property("text") == ""
        QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.Paste))
        assert item.property("text") == TEXT
        for cluster in ("🇯🇵", "👩🏽‍💻", "👨‍👩‍👧‍👦"):
            item.setProperty("text", "a" + cluster)
            focused.setProperty("cursorPosition", len(("a" + cluster).encode("utf-16-le")) // 2)
            QTest.keyClick(window, Qt.Key.Key_Backspace)
            assert item.property("text") == "a"
            QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.Undo))
            assert item.property("text") == "a" + cluster
            QTest.keySequence(window, QKeySequence(QKeySequence.StandardKey.Redo))
            assert item.property("text") == "a"
            item.setProperty("text", cluster + "z")
            focused.setProperty("cursorPosition", 0)
            QTest.keyClick(window, Qt.Key.Key_Delete)
            assert item.property("text") == "z"
        if which == "field":
            assert field.property("edits") >= 3

        item.setProperty("text", "a🇯🇵")
        focused.setProperty("cursorPosition", 5)
        focused.setProperty("readOnly", True)
        QTest.keyClick(window, Qt.Key.Key_Backspace)
        assert item.property("text") == "a🇯🇵"
        assert focused.property("selectionStart") == 5
        focused.setProperty("readOnly", False)

        # A native cursor move skips the flag as a unit. Selection/positions set by
        # application code can still land inside it; deletion expands those too.
        QTest.keyClick(window, Qt.Key.Key_Left)
        assert focused.property("cursorPosition") == 1
        focused.setProperty("cursorPosition", 3)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        assert item.property("text") == "a"
        item.setProperty("text", "a🇯🇵")
        focused.setProperty("cursorPosition", 5)

        # The preedit belongs to the IME. Preparing a delete must not select committed
        # text behind it; real OS candidate-window behavior still needs device checks.
        QGuiApplication.sendEvent(window, QInputMethodEvent("拼", []))
        assert focused.property("inputMethodComposing")
        TextEditing().prepareDeletion(focused, Qt.Key.Key_Backspace, 0)
        assert focused.property("selectionStart") == 5
        assert focused.property("selectionEnd") == 5
        QGuiApplication.sendEvent(window, QInputMethodEvent("", []))
    finally:
        clipboard.setText(original)
