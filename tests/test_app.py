import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import MAIN_QML, load_ui
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.composition import ViewModels, compose
from tests.support.i18n import ui_language
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

TRAY_QML = MAIN_QML.parent / "components" / "TrayIcon.qml"


def texts(item: QQuickItem) -> set[str]:
    """Every ``text`` shown under ``item``."""
    found = {text} if isinstance(text := item.property("text"), str) and text else set()
    for child in item.childItems():
        found |= texts(child)
    return found


class Loaded:
    def __init__(
        self,
        view_models: ViewModels,
        engine: QQmlApplicationEngine,
        window: QQuickWindow,
        warnings: list[str],
    ) -> None:
        self.view_models = view_models
        self.engine = engine
        self.window = window
        self.warnings = warnings

    def focused_text(self) -> str | None:
        item = self.window.activeFocusItem()
        return None if item is None else item.property("text")

    def key(self, qtbot: QtBot, key: Qt.Key) -> None:
        QTest.keyClick(self.window, key)
        qtbot.wait(250)  # longer than any overlay transition


@pytest.fixture
def loaded(request: pytest.FixtureRequest, qtbot: QtBot, tmp_path: Path) -> Iterator[Loaded]:
    """The main window in Chinese, or in the language given by indirect parametrization."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language(getattr(request, "param", "zh")):
        view_models = compose(SimulatedHost(), store, SimulatedFetcher())
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        try:
            assert load_ui(engine, view_models)
            (window,) = engine.rootObjects()
            assert isinstance(window, QQuickWindow)
            view_models.start()
            yield Loaded(view_models, engine, window, warnings)
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine


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


@pytest.mark.parametrize(
    ("loaded", "labels"),
    [("zh", ("接管方式", "关于", "GPL-3.0-only")), ("en", ("Inbound", "About", "GPL-3.0-only"))],
    indirect=["loaded"],
)
def test_settings_page_loads_without_qml_warnings(
    qtbot: QtBot, loaded: Loaded, labels: tuple[str, ...]
) -> None:
    settings = loaded.view_models.settings
    loaded.view_models.shell.setPage("settings")
    qtbot.waitUntil(lambda: settings.property("coreVersion") == "mihomo v1.19.33")
    shown = texts(loaded.window.contentItem())
    assert set(labels) <= shown
    assert "mihomo v1.19.33" in shown
    assert loaded.warnings == []


def cramped(item: QQuickItem) -> list[set[str]]:
    """The texts of the rows under ``item`` that need more width than they have, so that
    something in them is elided, wraps or runs under its neighbour."""
    found: list[set[str]] = []
    if item.metaObject().className().startswith("GroupRow"):
        content = item.childItems()[-1]  # the RowLayout after the divider
        if content.implicitWidth() > content.width() + 1:
            found.append(texts(item))
    for child in item.childItems():
        if child.isVisible():
            found += cramped(child)
    return found


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_settings_page_fits_the_smallest_window(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    window.setWidth(window.minimumWidth())
    window.setHeight(window.minimumHeight())
    settings = loaded.view_models.settings
    settings.setMixedPort("80")  # the longest hints, in every group
    settings.setAllowLan(True)
    loaded.view_models.shell.setPage("settings")
    qtbot.waitUntil(lambda: settings.property("allowLan"))
    qtbot.wait(100)
    assert cramped(window.contentItem()) == []

    window.setMinimumWidth(480)  # the check finds rows that do not fit
    window.setWidth(480)
    qtbot.wait(100)
    assert cramped(window.contentItem()) != []


def test_closing_the_window_quits_without_a_tray(qtbot: QtBot, loaded: Loaded) -> None:
    desktop = loaded.view_models.desktop
    assert not desktop.property("trayAvailable")  # the offscreen platform has none
    with qtbot.waitSignal(desktop.quitRequested):
        loaded.window.close()


def test_a_second_launch_shows_the_window(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    window.hide()
    assert not window.isVisible()
    loaded.view_models.desktop.showWindow()
    qtbot.waitUntil(window.isVisible)


def test_tray_icon_compiles(qtbot: QtBot) -> None:
    """The offscreen platform has no tray to create it on; compile it at least."""
    engine = QQmlApplicationEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(TRAY_QML)))
    assert component.isReady(), component.errorString()
