from functools import partial

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QColor, QFont
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from solaris.app import MAIN_QML, switch_language
from solaris.i18n import Translations
from solaris.viewmodels import settings as settings_view_model
from solaris.viewmodels.composition import ViewModels
from tests.support.appearance import SystemColorScheme
from tests.support.window import Loaded, find, texts

TRAY_QML = MAIN_QML.parent / "components" / "TrayIcon.qml"


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
    view_models.profiles.useRules("sample-b")
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
    ("zh", {"cancel": "取消", "delete": "删除", "3": "3 份订阅", "2": "2 份订阅"}),
    (
        "en",
        {"cancel": "Cancel", "delete": "Delete", "3": "3 subscriptions", "2": "2 subscriptions"},
    ),
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
    assert profiles.property("model").rows[1].hasOverride
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


@pytest.fixture
def all_networks(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Whether the platform has "include all networks" (D19), as indirect parametrization
    says; request it before ``loaded``, which reads it as the page loads."""
    monkeypatch.setattr(settings_view_model, "INCLUDE_ALL_NETWORKS", request.param)
    return request.param


@pytest.mark.parametrize("all_networks", [True, False], indirect=True)
def test_settings_page_offers_the_tun_stack(
    qtbot: QtBot, all_networks: bool, loaded: Loaded
) -> None:
    """The stack on every platform; "include all networks" only where macOS has it."""
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()
    qtbot.waitUntil(lambda: find(content, lambda i: i.property("text") == "TUN 协议栈") is not None)
    assert find(content, lambda i: i.property("text") == "跟随配置") is not None
    row = find(content, lambda i: i.property("text") == "包含所有网络")
    assert (row is not None) == all_networks
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


def test_language_switches_while_running(qtbot: QtBot, loaded: Loaded) -> None:
    view_models = loaded.view_models
    content = loaded.window.contentItem()
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    translations = Translations(QCoreApplication.instance())  # pyright: ignore[reportArgumentType]
    switch = partial(switch_language, translations, loaded.engine, view_models)
    view_models.settings.languageChanged.connect(switch)
    try:
        view_models.settings.setLanguage("en")
        qtbot.waitUntil(lambda: "Outbound" in texts(content))
        shown = texts(content)
        assert {"Overview", "Settings", "Connected", "Disconnect"} <= shown  # QML and Python
        assert "出站" not in shown
        assert view_models.shell.property("pages")[0]["label"] == "Overview"
        assert view_models.shell.property("serviceText") == "Service running"
        assert [m["label"] for m in view_models.overview.property("modeOptions")] == [
            "Rule",
            "Global",
            "Direct",
        ]
        assert view_models.proxies.property("sortOptions")[0]["label"] == "Default"
        assert view_models.profiles.property("summary") == "3 subscriptions"
        assert view_models.profiles.property("autoUpdateLabel") == "Update every 12 hours"
        assert view_models.settings.property("languageOptions")[0]["label"] == "System"

        view_models.settings.setLanguage("zh")
        qtbot.waitUntil(lambda: "出站" in texts(content))
        assert "Outbound" not in texts(content)
        assert view_models.profiles.property("summary") == "3 份订阅"
    finally:
        view_models.settings.languageChanged.disconnect(switch)
        translations.remove()
    assert loaded.warnings == []


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


@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_routing_page_loads_without_qml_warnings(qtbot: QtBot, loaded: Loaded) -> None:
    """Both routing modes, both tabs and the add-rule dialog's kinds (docs/routing.md §4.3)."""
    view_models = loaded.view_models
    routing = view_models.routing
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage("routing")
    qtbot.waitUntil(lambda: routing.property("profileRunning"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: len(routing.property("rules").rows) == 6)
    page = loaded.item(lambda i: i.metaObject().className().startswith("RoutingPage"))
    page.setProperty("tab", "groups")
    qtbot.wait(50)
    page.setProperty("tab", "rules")
    editor = routing._editor
    with qtbot.waitSignal(editor.opened):
        editor.openAdd()
    for kind in ("category", "domain", "ip", "service"):
        editor.setKind(kind)
        qtbot.wait(20)
    loaded.key(qtbot, Qt.Key.Key_Escape)
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("profile")
    view_models.shell.setPage("proxies")
    qtbot.waitUntil(lambda: view_models.proxies.property("currentGroup") != "")
    assert loaded.warnings == []
