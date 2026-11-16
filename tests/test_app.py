import asyncio
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QColor, QFont
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from solaris.app import MAIN_QML, switch_language
from solaris.desktop.dock import ACCESSORY, REGULAR
from solaris.desktop.instance import LOCK_NAME
from solaris.host.base import TAKEOVER_NONE, TAKEOVER_SETTINGS, TAKEOVER_TUN, HostError, Rule
from solaris.i18n import Translations
from solaris.models.settings import DatabaseSettingsStore
from solaris.models.store import ProfileStore
from solaris.viewmodels import settings as settings_view_model
from solaris.viewmodels.composition import ViewModels
from tests.support.appearance import SystemColorScheme
from tests.support.dock import ACTIVATE, RecordingApplication
from tests.support.simulated import CORE_VERSION, SimulatedHost
from tests.support.window import Loaded, find, find_all, texts

TRAY_QML = MAIN_QML.parent / "components" / "TrayIcon.qml"
ROOT = Path(__file__).resolve().parents[1]

# The UI as main() runs it, in a process of its own, against the simulated host. It says
# when its window has loaded, and what it asked of the host.
RUNNING_UI = """
import sys
from pathlib import Path
from solaris import app
from solaris.models import credentials
from solaris.models.store import ProfileStore
from tests.support.credentials import MemoryKeyring
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost


class Host(SimulatedHost):
    async def stop(self) -> None:
        print("stopped", flush=True)
        await super().stop()

    async def close(self) -> None:
        print("closed", flush=True)
        await super().close()


def load_ui(engine, view_models):
    loaded = load(engine, view_models)
    print("loaded", flush=True)
    return loaded


load, app.load_ui = app.load_ui, load_ui
keyring = MemoryKeyring()
credentials.system_keyring = lambda: keyring
directory = Path(sys.argv[1])
del sys.argv[1:]
store = ProfileStore(directory)
sys.exit(app.main(Host(), store, SimulatedFetcher(), updater=offline_updater(directory)))
"""

# ``Solaris --quit``, with the data directory in the test's.
QUIT = """
import sys
from pathlib import Path
from solaris import app
directory = Path(sys.argv[1])
app.default_data_dir = lambda: directory
sys.argv[1:] = ["--quit"]
sys.exit(app.main())
"""

# A window that hides instead of closing, as the main window does with a tray.
HIDING_WINDOW = """
import sys
from PySide6.QtCore import QTimer
from PySide6.QtQml import QQmlApplicationEngine
from solaris.app import Application
app = Application(sys.argv)
app.setQuitOnLastWindowClosed(False)
engine = QQmlApplicationEngine()
engine.loadData(
    b"import QtQuick\\n"
    b"Window { visible: true; onClosing: close => { close.accepted = false; hide() } }\\n"
)
QTimer.singleShot(0, app.quit)
QTimer.singleShot(10_000, lambda: app.exit(3))
sys.exit(app.exec())
"""

# A Quit event, as Qt sends for ⌘Q, the Dock's Quit and the system's quit at logout: the
# user's goes to user_quit (exit code 4 here), the system's leaves at once (0).
QUIT_EVENT = """
import sys
from PySide6.QtCore import QTimer
from solaris import app
ending = sys.argv[1] == "system"
app.session_ending = lambda: ending
application = app.Application(sys.argv)
application.user_quit = lambda: application.exit(4)
QTimer.singleShot(0, application.quit)
QTimer.singleShot(10_000, lambda: application.exit(3))
sys.exit(application.exec())
"""


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
    for family in (
        "inter",
        "jetbrains-mono",
        "noto-sans-sc",
        "noto-sans-symbols-2",
        "noto-color-emoji",
    ):
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
    qtbot.waitUntil(lambda: settings.property("coreVersion") == "v1.19.33")
    shown = texts(loaded.window.contentItem())
    assert set(labels) <= shown
    assert "v1.19.33" in shown
    assert loaded.warnings == []


def test_the_check_interval_shows_only_with_automatic_checks(qtbot: QtBot, loaded: Loaded) -> None:
    about = loaded.view_models.about
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()

    def interval() -> QQuickItem | None:
        return find(content, lambda i: i.property("text") == "检查频率")

    qtbot.waitUntil(lambda: interval() is not None)  # automatic checks are on by default
    assert not {"服务", "控制协议", CORE_VERSION, "项目主页"} & texts(content)
    about.setAutoCheck(False)
    qtbot.waitUntil(lambda: interval() is None)
    about.setAutoCheck(True)
    qtbot.waitUntil(lambda: interval() is not None)
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
    assert find(content, lambda i: i.property("text") == "mips") is not None  # the default
    row = find(content, lambda i: i.property("text") == "包含所有网络")
    assert (row is not None) == all_networks
    assert loaded.warnings == []


def is_switch(item: QQuickItem) -> bool:
    return item.metaObject().className().startswith("AppSwitch")


def shown_texts(item: QQuickItem) -> set[str]:
    """Every ``text`` shown under ``item``, leaving out hidden items."""
    found = (i.property("text") for i in find_all(item, lambda _: True))
    return {text for text in found if isinstance(text, str) and text}


def shown_row(content: QQuickItem, label: str) -> set[str] | None:
    """The texts of the visible row labelled ``label``, or None."""
    text = find(content, lambda i: i.property("text") == label)
    while text is not None and not text.metaObject().className().startswith("GroupRow"):
        text = text.parentItem()
    return None if text is None else shown_texts(text)


@pytest.mark.parametrize(
    ("takeover", "tun", "system_proxy", "note"),
    [
        # The Windows service: both switches, on by default (D26).
        (TAKEOVER_SETTINGS, {"TUN 模式"}, {"系统代理"}, "连接时使用"),
        # The macOS extension always runs a TUN and never sets the system proxy.
        (TAKEOVER_TUN, {"TUN 模式", "macOS 上总是启用", "已启用"}, None, "连接时使用"),
        # The development service takes over nothing.
        (TAKEOVER_NONE, {"TUN 模式", "不使用"}, {"系统代理", "不使用"}, "开发模式只开放混合端口"),
    ],
    indirect=["takeover"],
)
def test_settings_page_shows_what_connecting_takes_over(
    qtbot: QtBot,
    takeover: str,
    tun: set[str],
    system_proxy: set[str] | None,
    note: str,
    loaded: Loaded,
) -> None:
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()
    qtbot.waitUntil(lambda: shown_row(content, "TUN 模式") is not None)
    assert shown_row(content, "TUN 模式") == tun
    assert shown_row(content, "系统代理") == system_proxy
    assert find(content, lambda i: i.property("text") == note) is not None
    # Only the Windows service's rows have switches: the TUN and system proxy, both on.
    group = find(content, lambda i: i.property("text") == "TUN 协议栈")
    while group is not None and not group.metaObject().className().startswith("Group_"):
        group = group.parentItem()
    assert group is not None
    on = [s for s in find_all(group, is_switch) if s.property("value")]
    assert len(on) == (2 if takeover == TAKEOVER_SETTINGS else 0)
    assert loaded.warnings == []


def test_settings_page_says_when_only_the_mixed_port_serves(qtbot: QtBot, loaded: Loaded) -> None:
    overview = loaded.view_models.overview
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()
    hint = "两项都关闭时，连接后只开放混合端口"  # noqa: RUF001 - the UI's copy
    qtbot.waitUntil(lambda: overview.property("connected"))
    assert hint not in (shown_row(content, "系统代理") or set())
    overview.setTun(False)
    overview.setSystemProxy(False)
    qtbot.waitUntil(lambda: hint in (shown_row(content, "系统代理") or set()))
    assert loaded.warnings == []


def test_settings_page_says_udp_needs_the_tun(qtbot: QtBot, loaded: Loaded) -> None:
    overview = loaded.view_models.overview
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()
    hint = "游戏、语音通话等 UDP 流量不经过系统代理，需要 TUN 模式"  # noqa: RUF001 - the UI's copy
    qtbot.waitUntil(lambda: overview.property("connected"))
    assert hint not in (shown_row(content, "系统代理") or set())
    overview.setTun(False)
    qtbot.waitUntil(lambda: hint in (shown_row(content, "系统代理") or set()))
    overview.setSystemProxy(False)
    qtbot.waitUntil(lambda: hint not in (shown_row(content, "系统代理") or set()))
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
        assert {"Overview", "Settings", "Connected", "Connect"} <= shown  # QML and Python
        assert "出站" not in shown
        assert view_models.shell.property("pages").rows[0].label == "Overview"
        assert view_models.shell.property("serviceText") == "Service running"
        assert [m["label"] for m in view_models.overview.property("modeOptions")] == [
            "Rule",
            "Global",
            "Direct",
        ]
        assert view_models.proxies.property("sortOptions")[0]["label"] == "Default"
        assert view_models.profiles.property("summary") == "3 subscriptions"
        assert (
            view_models.profiles.property("model").rows[0].autoUpdateLabel
            == "Update every 12 hours"
        )
        assert view_models.settings.property("languageOptions")[0]["label"] == "System"

        view_models.settings.setLanguage("zh")
        qtbot.waitUntil(lambda: "出站" in texts(content))
        assert "Outbound" not in texts(content)
        assert view_models.profiles.property("summary") == "3 份订阅"
    finally:
        view_models.settings.languageChanged.disconnect(switch)
        translations.remove()
    assert loaded.warnings == []


def simulated(loaded: Loaded) -> SimulatedHost:
    host = loaded.view_models.host
    assert isinstance(host, SimulatedHost)
    return host


def running(host: SimulatedHost) -> bool:
    return asyncio.run(host.get_status()).state == "running"


def test_closing_the_window_quits_without_a_tray(qtbot: QtBot, loaded: Loaded) -> None:
    """Quitting disconnects first (D41)."""
    desktop, overview = loaded.view_models.desktop, loaded.view_models.overview
    assert not desktop.property("trayAvailable")  # the offscreen platform has none
    qtbot.waitUntil(lambda: overview.property("connected"))
    with qtbot.waitSignal(desktop.quitRequested):
        loaded.window.close()
        assert not loaded.window.isVisible()
    assert not running(simulated(loaded))


def test_closing_the_window_into_the_tray_keeps_the_tunnel(qtbot: QtBot, loaded: Loaded) -> None:
    desktop, overview = loaded.view_models.desktop, loaded.view_models.overview
    qtbot.waitUntil(lambda: overview.property("connected"))
    # The offscreen platform has no tray to show; the window behaves as with one.
    desktop._tray = True
    desktop.closeActionChanged.emit()
    assert loaded.view_models.settings_store.current.close_action == "tray"
    assert desktop.property("hideOnClose")
    with qtbot.assertNotEmitted(desktop.quitRequested, wait=500):
        loaded.window.close()
    assert not loaded.window.isVisible()
    assert running(simulated(loaded))
    assert not overview.property("busy")
    loaded.view_models.desktop.showWindow()
    qtbot.waitUntil(loaded.window.isVisible)


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_a_failed_disconnect_asks_before_quitting(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    desktop, overview = loaded.view_models.desktop, loaded.view_models.overview
    host = simulated(loaded)
    qtbot.waitUntil(lambda: overview.property("connected"))
    english = request.node.callspec.params["loaded"] == "en"
    title = "Couldn’t disconnect" if english else "未能断开连接"  # noqa: RUF001 - the UI's copy

    async def unresponsive() -> None:
        raise HostError("core_unresponsive", "the core did not answer")

    monkeypatch.setattr(host, "stop", unresponsive)

    def ask() -> None:
        with qtbot.waitSignal(desktop.quitFailed):
            loaded.window.close()
        qtbot.waitUntil(loaded.window.isVisible)  # back, to ask
        qtbot.waitUntil(lambda: loaded.focused_text() == ("Retry" if english else "重试"))
        assert loaded.overlay_items(lambda item: item.property("text") == title)

    # Esc keeps Solaris running, the tunnel as it was.
    ask()
    with qtbot.assertNotEmitted(desktop.quitRequested, wait=300):
        loaded.key(qtbot, Qt.Key.Key_Escape)
    assert desktop.property("quitMessage") == ""
    assert running(host)

    # Retry tries again; then quitting anyway leaves, the tunnel perhaps still running.
    ask()
    with qtbot.waitSignal(desktop.quitFailed):
        loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: loaded.focused_text() == ("Retry" if english else "重试"))
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() == ("Quit anyway" if english else "仍然退出")
    with qtbot.waitSignal(desktop.quitRequested):
        loaded.key(qtbot, Qt.Key.Key_Return)
    assert running(host)
    assert loaded.warnings == []


def in_the_menu_bar(loaded: Loaded) -> RecordingApplication:
    """The window as on macOS, which the offscreen platform is not: with a tray, and NSApp's
    activation policy recorded (desktop/dock.py)."""
    desktop = loaded.view_models.desktop
    application = RecordingApplication()
    desktop._tray = True
    desktop._application = application
    desktop.closeActionChanged.emit()
    return application


def test_every_way_back_to_the_window_puts_solaris_in_the_dock(
    qtbot: QtBot, loaded: Loaded
) -> None:
    """Hidden in the menu bar, Solaris is out of the Dock; whatever shows the window brings it
    back and activates it."""
    view_models, window = loaded.view_models, loaded.window
    desktop, overview = view_models.desktop, view_models.overview
    application = in_the_menu_bar(loaded)

    def hide() -> None:
        application.calls.clear()
        window.close()
        assert not window.isVisible()
        assert application.calls == [ACCESSORY]
        application.calls.clear()

    for show in (
        desktop.showWindow,  # the menu bar's 打开主窗口, or a second launch
        desktop.reopened.emit,  # Solaris opened again, as from the Finder
        lambda: view_models.open_link("solaris://install-config?url=https%3A%2F%2Fa.example"),
        lambda: view_models.open_link("clash://install-config?url=https%3A%2F%2Fa.example"),
        overview.extensionSetupRequested.emit,  # the system extension's approval guide
    ):
        hide()
        show()
        qtbot.waitUntil(window.isVisible)
        assert application.calls == [REGULAR, ACTIVATE]

    # Shown already: nothing changes, and a minimized window stays in the Dock.
    application.calls.clear()
    desktop.showWindow()
    window.showMinimized()
    qtbot.wait(100)
    assert window.isVisible()
    assert application.calls == []
    assert loaded.warnings == []


def test_a_failed_disconnect_brings_solaris_back_to_ask(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the window to quit takes the Dock icon with it while Solaris disconnects; the
    question when that fails brings both back."""
    desktop, overview = loaded.view_models.desktop, loaded.view_models.overview
    host = simulated(loaded)
    qtbot.waitUntil(lambda: overview.property("connected"))
    application = in_the_menu_bar(loaded)
    loaded.view_models.settings_store.update(close_action="quit")

    async def unresponsive() -> None:
        raise HostError("core_unresponsive", "the core did not answer")

    monkeypatch.setattr(host, "stop", unresponsive)
    with qtbot.waitSignal(desktop.quitFailed):
        loaded.window.close()
        assert application.calls == [ACCESSORY]
    qtbot.waitUntil(loaded.window.isVisible)
    assert application.calls == [ACCESSORY, REGULAR, ACTIVATE]
    loaded.key(qtbot, Qt.Key.Key_Escape)  # stay
    assert running(host)


def test_a_second_launch_shows_the_window(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    window.hide()
    assert not window.isVisible()
    loaded.view_models.desktop.showWindow()
    qtbot.waitUntil(window.isVisible)


def test_quit_command_closes_the_running_ui(tmp_path: Path) -> None:
    ui = subprocess.Popen(
        [sys.executable, "-c", RUNNING_UI, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    quit_command = [sys.executable, "-c", QUIT, str(tmp_path)]
    try:
        assert ui.stdout is not None
        assert ui.stdout.readline() == "loaded\n"
        assert subprocess.run(quit_command, cwd=ROOT, timeout=60).returncode == 0
        assert ui.wait(timeout=30) == 0
        said = ui.stdout.read().split()
    finally:
        ui.kill()
        ui.wait()
    # It let go of the host and left the tunnel as it was: the installer's --quit is no
    # quitting by the user (D41).
    assert said == ["closed"]
    # It closed the database, then let go of the lock: no write-ahead log, no lock file.
    assert not (tmp_path / "solaris.db-wal").exists()
    assert not (tmp_path / LOCK_NAME).exists()
    # With no UI running, the command has nothing to wait for.
    assert subprocess.run(quit_command, cwd=ROOT, timeout=30).returncode == 0


@pytest.mark.parametrize(
    ("existing", "saved", "expected"),
    [(False, None, False), (True, None, True), (True, True, True), (True, False, False)],
)
def test_windows_startup_uses_tun_only_for_new_installs(
    tmp_path: Path, existing: bool, saved: bool | None, expected: bool
) -> None:
    if existing:
        store = ProfileStore(tmp_path)
        if saved is not None:
            with store.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO settings VALUES ('system_proxy', ?, 0)",
                    ("true" if saved else "false",),
                )
        store.close()

    startup = """
import sys
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QTimer

from solaris import app
from solaris.host.base import TAKEOVER_SETTINGS
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost

directory = Path(sys.argv.pop(1))
app.default_data_dir = lambda: directory
app.sys = SimpleNamespace(platform="win32", argv=sys.argv)
load_ui = app.load_ui

def load(engine, view_models):
    print(view_models.overview.tunEnabled, view_models.overview.systemProxyEnabled)
    QTimer.singleShot(0, lambda: QCoreApplication.exit(0))
    return load_ui(engine, view_models)

app.load_ui = load
sys.exit(app.main(
    host=SimulatedHost(takeover=TAKEOVER_SETTINGS),
    fetcher=SimulatedFetcher(),
    updater=offline_updater(directory),
))
"""
    result = subprocess.run(
        [sys.executable, "-c", startup, str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.splitlines() == [f"True {expected}"]
    reopened = ProfileStore(tmp_path)
    try:
        options = DatabaseSettingsStore(reopened.database).current.run_options()
        assert (options.tun_enabled, options.system_proxy_enabled) == (True, expected)
    finally:
        reopened.close()


def test_quitting_leaves_even_when_the_window_hides_instead() -> None:
    """Qt closes the windows before it quits and gives up when one refuses; the system's
    quit leaves anyway."""
    assert subprocess.run([sys.executable, "-c", HIDING_WINDOW], timeout=60).returncode == 0


@pytest.mark.parametrize(("source", "code"), [("user", 4), ("system", 0)])
def test_the_users_quit_event_disconnects_first(source: str, code: int) -> None:
    """⌘Q and the Dock's Quit go to DesktopViewModel.quit, which disconnects first (D41); the
    system's quit at the end of the session leaves at once (session.session_ending)."""
    quit_event = [sys.executable, "-c", QUIT_EVENT, source]
    assert subprocess.run(quit_event, cwd=ROOT, timeout=60).returncode == code


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
    qtbot.waitUntil(lambda: routing._profile_routes.property("running"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: len(routing.plan_rules().property("rows").rows) == 6)
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


def test_a_profile_with_thousands_of_rules_makes_only_the_rows_in_sight(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching to a profile's own rules while connected lists them without holding up the
    window: a row each for ten thousand rules took seconds (docs/routing.md §4.3)."""
    view_models = loaded.view_models
    routing = view_models.routing
    rules = [Rule("DOMAIN-SUFFIX", f"site{n}.example.com", "节点选择", "") for n in range(5000)]
    rules.append(Rule("MATCH", "", "DIRECT", ""))

    async def list_rules() -> list[Rule]:
        return rules

    monkeypatch.setattr(view_models.host, "list_rules", list_rules)
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage("routing")
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: not routing.property("busy"))
    routing.setMode("profile")
    qtbot.waitUntil(lambda: len(routing._profile_routes.property("rules").rows) == len(rules))
    qtbot.wait(50)
    page = loaded.item(lambda i: i.metaObject().className().startswith("RoutingPage"))

    def made() -> dict[str, QQuickItem]:
        found = find_all(page, lambda i: isinstance(i.property("match"), str))
        return {i.property("match"): i for i in found}

    shown = made()
    assert 0 < len(shown) < 200
    assert loaded.on_screen(shown["DOMAIN-SUFFIX,site0.example.com"])
    # Scrolled to the end, the last rows are made in their place.
    _, rule_list = find_all(page, lambda i: i.metaObject().className() == "QQuickListView")
    end = rule_list.property("originY") + rule_list.property("contentHeight") - rule_list.height()
    rule_list.setProperty("contentY", end)
    qtbot.wait(50)
    shown = made()
    assert len(shown) < 200
    assert loaded.on_screen(shown["MATCH"])
    assert "DOMAIN-SUFFIX,site0.example.com" not in shown
    assert loaded.warnings == []


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_vpngate_dialog_loads_without_qml_warnings(qtbot: QtBot, loaded: Loaded) -> None:
    """The VPN Gate exit's dialog with its countries and servers (docs/exits.md §6)."""
    view_models = loaded.view_models
    routing = view_models.routing
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage("routing")
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    page = loaded.item(lambda i: i.metaObject().className().startswith("RoutingPage"))
    page.setProperty("tab", "groups")
    exit_ = routing._vpngate
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: exit_.property("servers").rowCount() == 5)
    with qtbot.waitSignal(exit_.opened):
        exit_.open()
    qtbot.wait(100)
    exit_.toggleCountry("KR")
    qtbot.waitUntil(lambda: exit_.property("severalCountries"))
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert loaded.warnings == []
