import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import MAIN_QML, load_ui
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.appearance import Appearance
from mihomo_desktop.viewmodels.composition import ViewModels, compose
from tests.support.appearance import SystemColorScheme
from tests.support.i18n import ui_language
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles


def find(item: QQuickItem, test: Callable[[QQuickItem], bool]) -> QQuickItem | None:
    """The first visible item under ``item`` that passes ``test``."""
    if item.isVisible() and test(item):
        return item
    for child in item.childItems():
        if (found := find(child, test)) is not None:
            return found
    return None


def texts(item: QQuickItem) -> set[str]:
    """Every ``text`` shown under ``item``."""
    found = {text} if isinstance(text := item.property("text"), str) and text else set()
    for child in item.childItems():
        found |= texts(child)
    return found


class Loaded:
    def __init__(self, view_models: ViewModels, window: QQuickWindow, warnings: list[str]) -> None:
        self.view_models = view_models
        self.window = window
        self.warnings = warnings

    def focused_text(self) -> str | None:
        item = self.window.activeFocusItem()
        return None if item is None else item.property("text")

    def key(self, qtbot: QtBot, key: Qt.Key) -> None:
        QTest.keyClick(self.window, key)
        qtbot.wait(250)  # longer than any overlay transition

    def click(self, qtbot: QtBot, item: QQuickItem, x: float | None = None) -> None:
        middle = QPointF(item.width() / 2 if x is None else x, item.height() / 2)
        point = item.mapToScene(middle).toPoint()
        button, modifier = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        QTest.mouseClick(self.window, button, modifier, point)
        qtbot.wait(250)

    def item(self, test: Callable[[QQuickItem], bool]) -> QQuickItem:
        found = find(self.window.contentItem(), test)
        assert found is not None
        return found


@pytest.fixture
def system() -> SystemColorScheme:
    """The system's appearance, light unless a test switches it."""
    return SystemColorScheme()


@pytest.fixture
def appearance(request: pytest.FixtureRequest, system: SystemColorScheme) -> Appearance:
    """Following the system, or the mode given by indirect parametrization."""
    return Appearance(getattr(request, "param", "system"), hints=system)


@pytest.fixture
def loaded(
    request: pytest.FixtureRequest, qtbot: QtBot, tmp_path: Path, appearance: Appearance
) -> Iterator[Loaded]:
    """The main window in Chinese, or in the language given by indirect parametrization."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language(getattr(request, "param", "zh")):
        view_models = compose(SimulatedHost(), store, SimulatedFetcher(), appearance=appearance)
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        try:
            assert load_ui(engine, view_models)
            (window,) = engine.rootObjects()
            assert isinstance(window, QQuickWindow)
            view_models.start()
            yield Loaded(view_models, window, warnings)
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine


@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
@pytest.mark.parametrize(
    ("loaded", "label"), [("zh", "出站"), ("en", "Outbound")], indirect=["loaded"]
)
def test_main_window_loads_without_qml_warnings(qtbot: QtBot, loaded: Loaded, label: str) -> None:
    view_models = loaded.view_models
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    assert label in texts(loaded.window.contentItem())
    view_models.shell.setPage("proxies")
    qtbot.waitUntil(lambda: view_models.proxies.property("currentGroup") != "")
    view_models.shell.setPage("profiles")
    view_models.profiles.use("sample-b")
    qtbot.waitUntil(lambda: view_models.profiles.property("activeName") == "示例订阅 B")
    view_models.shell.setPage("connections")
    qtbot.waitUntil(lambda: view_models.connections.property("total") > 0)
    view_models.connections.sortBy("rate")
    view_models.shell.setPage("logs")
    qtbot.waitUntil(lambda: view_models.logs.property("count") > 0)
    view_models.logs.setLevel("debug")
    assert loaded.warnings == []


# The dialog's buttons and the profile count, in each language.
DIALOG_TEXT = [
    ("zh", {"cancel": "取消", "delete": "删除", "3": "3 个配置", "2": "2 个配置"}),
    ("en", {"cancel": "Cancel", "delete": "Delete", "3": "3 profiles", "2": "2 profiles"}),
]


@pytest.mark.parametrize(("loaded", "text"), DIALOG_TEXT, indirect=["loaded"], ids=["zh", "en"])
def test_removal_dialog_keeps_the_profile_by_default(
    qtbot: QtBot, loaded: Loaded, text: dict[str, str]
) -> None:
    profiles = loaded.view_models.profiles
    loaded.view_models.shell.setPage("profiles")

    def ask() -> None:
        profiles.requestRemove("sample-b")
        qtbot.waitUntil(lambda: loaded.focused_text() == text["cancel"])  # the safe default

    ask()
    loaded.key(qtbot, Qt.Key.Key_Escape)
    ask()
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert profiles.property("summary") == text["3"]

    ask()
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() == text["delete"]
    loaded.key(qtbot, Qt.Key.Key_Tab)  # focus stays in the dialog
    assert loaded.focused_text() == text["cancel"]
    loaded.key(qtbot, Qt.Key.Key_Tab)
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: profiles.property("summary") == text["2"])
    assert loaded.warnings == []


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_override_editor_saves_with_ctrl_enter(qtbot: QtBot, loaded: Loaded) -> None:
    profiles = loaded.view_models.profiles
    loaded.view_models.shell.setPage("profiles")
    profiles.editOverride("sample-b")

    def editor_focused() -> bool:
        item = loaded.window.activeFocusItem()
        return item is not None and item.metaObject().className().startswith("TextArea")

    qtbot.waitUntil(editor_focused)  # the dialog opens on the editor
    editor = loaded.window.activeFocusItem()
    assert editor is not None

    # Enter types a new line; a rejected override keeps the dialog open.
    editor.setProperty("text", "mode: [")
    editor.setProperty("cursorPosition", 7)
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert loaded.focused_text() == "mode: [\n"
    QTest.keyClick(loaded.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: profiles.property("overrideError") != "")
    assert loaded.window.activeFocusItem() is editor

    editor.setProperty("text", "mode: direct\n")
    with qtbot.waitSignal(profiles.overrideSaved):
        QTest.keyClick(loaded.window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: loaded.window.activeFocusItem() is not editor)
    assert profiles.property("profiles")[1]["hasOverride"]
    assert loaded.warnings == []


def test_the_window_repaints_when_the_system_turns_dark(
    qtbot: QtBot, loaded: Loaded, system: SystemColorScheme
) -> None:
    def background() -> str:
        color = loaded.window.property("color")
        assert isinstance(color, QColor)
        return color.name().upper()

    assert background() == "#F3F4F6"
    system.switch(Qt.ColorScheme.Dark)
    assert background() == "#1B1D20"
    loaded.view_models.appearance.setMode("light")
    assert background() == "#F3F4F6"
    assert loaded.warnings == []


def test_the_bundled_fonts_are_used(loaded: Loaded) -> None:
    font = loaded.window.property("font")
    assert isinstance(font, QFont)
    assert font.family() == "Inter"
    fonts = MAIN_QML.parent / "fonts"
    for family in ("inter", "jetbrains-mono"):
        assert (fonts / family / "OFL.txt").is_file()  # the license travels with the fonts


CLOSE_ALL_TEXT = [
    ("zh", {"button": "全部关闭", "cancel": "取消"}),
    ("en", {"button": "Close all", "cancel": "Cancel"}),
]


def connection_ids(view_models: ViewModels) -> set[str]:
    return {row.connectionId for row in view_models.connections.property("model").rows}


@pytest.mark.parametrize(("loaded", "text"), CLOSE_ALL_TEXT, indirect=["loaded"], ids=["zh", "en"])
def test_close_all_asks_first_and_keeps_connections_by_default(
    qtbot: QtBot, loaded: Loaded, text: dict[str, str]
) -> None:
    connections = loaded.view_models.connections
    loaded.view_models.shell.setPage("connections")
    qtbot.waitUntil(lambda: connections.property("total") > 0)
    button = loaded.item(lambda i: i.property("text") == text["button"])

    before = connection_ids(loaded.view_models)

    loaded.click(qtbot, button)
    assert loaded.focused_text() == text["cancel"]  # the safe default
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.wait(1200)
    assert before <= connection_ids(loaded.view_models)

    loaded.click(qtbot, button)
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() == text["button"]  # the dialog's own button now
    loaded.key(qtbot, Qt.Key.Key_Return)
    # New visits may open meanwhile; every connection that was open has gone.
    qtbot.waitUntil(lambda: not before & connection_ids(loaded.view_models))
    assert loaded.warnings == []


def test_connections_table_works_from_the_keyboard(qtbot: QtBot, loaded: Loaded) -> None:
    connections = loaded.view_models.connections
    loaded.view_models.shell.setPage("connections")
    qtbot.waitUntil(lambda: connections.property("total") >= 3)
    rows = connections.property("model").rows
    first, second = rows[0].connectionId, rows[1].connectionId

    # Clicking a row selects it and shows its detail; Esc closes the detail and leaves the
    # table focused for the arrow keys.
    row = loaded.item(lambda i: i.property("connectionId") == first)
    loaded.click(qtbot, row, 40)
    detail = loaded.item(lambda i: i.property("text") in ("关闭连接",))
    assert detail.isVisible()
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert not detail.isVisible()
    loaded.key(qtbot, Qt.Key.Key_Down)
    assert connections.property("detail")["id"] == second

    # Enter opens the detail again; Delete closes the selected connection.
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert detail.isVisible()
    loaded.key(qtbot, Qt.Key.Key_Escape)
    loaded.key(qtbot, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: connections.property("detailClosed"))
    assert second not in connection_ids(loaded.view_models)
    assert loaded.warnings == []
