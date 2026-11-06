"""The sidebar's pages follow the tunnel: 代理 lists what only a running core answers, so it
shows while the connection switch is on and settled, and the window leaves it when the
tunnel stops (D27, DESIGN.md "侧栏导航")."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtQuick import QQuickItem
from pytestqt.qtbot import QtBot

from solaris.host.base import HostError, HostStatus
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded, accessible, find, find_all, kind

ALL = ["overview", "proxies", "profiles", "routing", "connections", "logs", "settings"]
WITHOUT_PROXIES = [page for page in ALL if page != "proxies"]
NOW = 1_800_000_000.0
Role = QAccessible.Role


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def listed(view_models: ViewModels) -> list[str]:
    return [row.page for row in prop(view_models.shell, "pages").rows]


@pytest.fixture
def view_models(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW)
    built.start()
    yield built
    built.shutdown()


def settled(view_models: ViewModels) -> bool:
    overview = view_models.overview
    return prop(overview, "connected") and not prop(overview, "busy")


def test_proxies_show_once_connected_and_go_as_the_switch_turns_off(
    qtbot: QtBot, tmp_path: Path
) -> None:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    view_models = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW)
    shell, overview = view_models.shell, view_models.overview
    try:
        # Nothing has said the tunnel runs yet.
        assert listed(view_models) == WITHOUT_PROXIES
        assert not prop(shell, "tunnelRunning")
        shell.setPage("proxies")
        assert prop(shell, "currentPage") == "overview"

        view_models.start()
        qtbot.waitUntil(lambda: settled(view_models))
        assert listed(view_models) == ALL
        assert prop(shell, "tunnelRunning")
        shell.setPage("proxies")
        assert prop(shell, "currentPage") == "proxies"
        assert prop(shell, "pageIndex") == 1

        # The switch goes off at once, and so do the page and its tab.
        overview.setConnected(False)
        assert prop(overview, "connectionChanging")
        assert listed(view_models) == WITHOUT_PROXIES
        assert (prop(shell, "currentPage"), prop(shell, "pageIndex")) == ("overview", 0)
        qtbot.waitUntil(lambda: not prop(overview, "busy"))
        shell.setPage("proxies")  # the overview's node row, a stale click
        assert prop(shell, "currentPage") == "overview"

        # Connecting: the switch is on, but no core answers until the tunnel runs.
        overview.setConnected(True)
        assert (prop(overview, "connectionOn"), prop(overview, "connectionChanging")) == (
            True,
            True,
        )
        assert listed(view_models) == WITHOUT_PROXIES
        qtbot.waitUntil(lambda: settled(view_models))
        assert listed(view_models) == ALL

        # Another page stays where it is when the tunnel stops.
        shell.setPage("logs")
        overview.setConnected(False)
        assert prop(shell, "currentPage") == "logs"
        qtbot.waitUntil(lambda: not prop(overview, "busy"))
    finally:
        view_models.shutdown()


def starting(host: SimulatedHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "_state", lambda: "starting")  # the service brings it back


def stopping(host: SimulatedHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "_state", lambda: "stopping")


def recovering(host: SimulatedHost, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows after sleep: the service suspended the core and brings it back (§5.2).
    monkeypatch.setattr(host, "_running", False)
    monkeypatch.setattr(host, "recovery_state", "connecting")


def failing(code: str) -> Callable[[SimulatedHost, pytest.MonkeyPatch], None]:
    def apply(host: SimulatedHost, monkeypatch: pytest.MonkeyPatch) -> None:
        async def status() -> HostStatus:
            raise HostError(code, code)

        monkeypatch.setattr(host, "get_status", status)

    return apply


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(starting, id="starting"),
        pytest.param(stopping, id="stopping"),
        pytest.param(recovering, id="recovering"),
        pytest.param(failing("owned_by_other_user"), id="other-user"),  # D5b
        pytest.param(failing("service_unavailable"), id="no-service"),
    ],
)
def test_proxies_go_while_no_running_core_of_this_user_answers(
    qtbot: QtBot,
    view_models: ViewModels,
    monkeypatch: pytest.MonkeyPatch,
    state: Callable[[SimulatedHost, pytest.MonkeyPatch], None],
) -> None:
    shell, overview, host = view_models.shell, view_models.overview, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: settled(view_models))
    shell.setPage("proxies")
    state(host, monkeypatch)
    overview.refresh()
    qtbot.waitUntil(lambda: "proxies" not in listed(view_models))
    assert prop(shell, "currentPage") == "overview"
    assert not prop(overview, "connected") or prop(overview, "connectionChanging")
    monkeypatch.undo()
    overview.refresh()
    qtbot.waitUntil(lambda: listed(view_models) == ALL)


# -- the window -------------------------------------------------------------------------------
def tabs(loaded: Loaded) -> list[QQuickItem]:
    """The sidebar's tabs, top to bottom."""
    found = find_all(loaded.window.contentItem(), lambda i: kind(i) == "NavItem")
    return sorted(found, key=lambda i: i.mapToScene(QPointF(0, 0)).y())


def tab(loaded: Loaded, page: str) -> QQuickItem:
    return loaded.item(lambda i: kind(i) == "NavItem" and i.property("page") == page)


def shown_page(loaded: Loaded) -> str:
    pages = {
        "OverviewPage": "overview",
        "ProxiesPage": "proxies",
        "ProfilesPage": "profiles",
        "RoutingPage": "routing",
        "ConnectionsPage": "connections",
        "LogsPage": "logs",
        "SettingsPage": "settings",
    }
    (shown,) = find_all(loaded.window.contentItem(), lambda i: kind(i) in pages)
    return pages[kind(shown)]


def disconnect(qtbot: QtBot, loaded: Loaded) -> None:
    overview = loaded.view_models.overview
    overview.toggleConnection()  # the tray's 断开
    qtbot.waitUntil(lambda: not prop(overview, "connected") and not prop(overview, "busy"))


def connect(qtbot: QtBot, loaded: Loaded) -> None:
    loaded.view_models.overview.toggleConnection()
    qtbot.waitUntil(lambda: settled(loaded.view_models))
    qtbot.wait(50)


def tab_order(qtbot: QtBot, loaded: Loaded) -> list[str]:
    """The tabs Tab visits from the first one."""
    first = tabs(loaded)[0]
    first.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    order = [first.property("page")]
    while True:
        loaded.key(qtbot, Qt.Key.Key_Tab, wait=10)
        focused = loaded.focused()
        if kind(focused) != "NavItem":
            return order
        order.append(focused.property("page"))


@pytest.mark.usefixtures("screen_reader")
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_the_sidebar_lists_proxies_while_connected(qtbot: QtBot, loaded: Loaded) -> None:
    view_models = loaded.view_models
    qtbot.waitUntil(lambda: settled(view_models))
    labels = {row.page: row.label for row in prop(view_models.shell, "pages").rows}
    overview_page = loaded.item(lambda i: kind(i) == "OverviewPage")

    def node_button() -> QQuickItem:
        prefix = "当前节点" if labels["overview"] == "概览" else "Current proxy"
        found = find(overview_page, lambda i: accessible(i).name.startswith(prefix))
        assert found is not None
        return found

    def check(pages: list[str]) -> None:
        assert [t.property("page") for t in tabs(loaded)] == pages
        assert tab_order(qtbot, loaded) == pages
        for item in tabs(loaded):
            page = item.property("page")
            assert (accessible(item).role, accessible(item).name) == (Role.PageTab, labels[page])
        assert node_button().isEnabled() == ("proxies" in pages)

    check(ALL)
    disconnect(qtbot, loaded)
    check(WITHOUT_PROXIES)
    connect(qtbot, loaded)
    check(ALL)
    assert loaded.warnings == []


def test_disconnecting_on_the_proxies_page_returns_to_the_overview(
    qtbot: QtBot, loaded: Loaded
) -> None:
    view_models = loaded.view_models
    shell = view_models.shell
    qtbot.waitUntil(lambda: settled(view_models))

    # Opened from the sidebar, the focus in the page's list of groups.
    loaded.click(qtbot, tab(loaded, "proxies"))
    assert (prop(shell, "currentPage"), shown_page(loaded)) == ("proxies", "proxies")
    qtbot.waitUntil(lambda: prop(view_models.proxies, "currentGroup") != "")
    page = loaded.item(lambda i: kind(i) == "ProxiesPage")
    row = find(page, lambda i: i.property("checked") is True and i.activeFocusOnTab())
    assert row is not None
    row.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    disconnect(qtbot, loaded)
    assert (prop(shell, "currentPage"), shown_page(loaded)) == ("overview", "overview")
    # The keyboard's focus goes to the overview's tab, with its ring.
    assert loaded.focused() is tab(loaded, "overview")
    assert loaded.focused().property("visualFocus")

    # The focus on the proxies tab itself, opened with the keyboard.
    connect(qtbot, loaded)
    tab(loaded, "proxies").forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Space)
    assert shown_page(loaded) == "proxies"
    disconnect(qtbot, loaded)
    assert shown_page(loaded) == "overview"
    assert loaded.focused() is tab(loaded, "overview")
    assert loaded.focused().property("visualFocus")

    # On another page, the focus on the proxies tab goes to that page's tab.
    connect(qtbot, loaded)
    shell.setPage("logs")
    tab(loaded, "proxies").forceActiveFocus(Qt.FocusReason.TabFocusReason)
    disconnect(qtbot, loaded)
    assert shown_page(loaded) == "logs"
    assert loaded.focused() is tab(loaded, "logs")
    assert loaded.focused().property("visualFocus")

    # Focus elsewhere stays.
    connect(qtbot, loaded)
    shell.setPage("proxies")
    settings = tab(loaded, "settings")
    settings.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    disconnect(qtbot, loaded)
    assert shown_page(loaded) == "overview"
    assert loaded.focused() is settings
    assert loaded.warnings == []
