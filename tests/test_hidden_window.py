"""The window lets its pages go while it hides in the tray or the menu bar, and loads them again
as it shows (docs/ui.md §8, "隐藏时释放"). The offscreen platform has no tray; the window behaves
as with one. It gives up its native window there as on macOS, except when the tests run on
Windows: DesktopViewModel keeps the native window on Windows (desktop/memory.py)."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QEvent, QMetaObject, QObject, QRectF, Qt
from PySide6.QtGui import QAccessible, QImage, QPlatformSurfaceEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from pytestqt.qtbot import QtBot

from solaris.app import load_ui
from solaris.desktop.titlebar import MAC, WINDOWS
from solaris.host.base import HostError
from solaris.models.store import ProfileStore
from solaris.viewmodels.appearance import Appearance
from solaris.viewmodels.composition import compose
from tests.support.i18n import ui_language
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import PAGES, Accessible, Loaded, accessible, every_item, kind, pages

LINK = "https://sub.example.com/a"


def in_the_tray(loaded: Loaded) -> None:
    desktop = loaded.view_models.desktop
    desktop._tray = True
    desktop.closeActionChanged.emit()
    assert desktop.property("hideOnClose")
    assert pages(loaded.window) == PAGES


def release(loaded: Loaded) -> None:
    """What the window does a moment after it hides."""
    QMetaObject.invokeMethod(loaded.window, "release")


def page(loaded: Loaded, name: str) -> QQuickItem:
    return loaded.item(lambda item: kind(item) == name)


class Surfaces(QObject):
    """The native window's comings and goings."""

    def __init__(self, window: QQuickWindow) -> None:
        super().__init__(window)
        self.events: list[QPlatformSurfaceEvent.SurfaceEventType] = []
        window.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if isinstance(event, QPlatformSurfaceEvent):
            self.events.append(event.surfaceEventType())
        return super().eventFilter(watched, event)


def shapes(item: QQuickItem) -> list[QQuickItem]:
    """The shapes under ``item``, shown or not: Icon's, CaptionButtons' and TrafficChart's."""
    return [found for found in every_item(item) if found.inherits("QQuickShape")]


def renewed(before: list[QQuickItem], after: list[QQuickItem]) -> bool:
    """``after`` are as many shapes as ``before``, and all of them new."""
    return len(after) == len(before) > 0 and not any(a is b for a in after for b in before)


def gone(items: list[QQuickItem]) -> bool:
    """Every one of ``items`` deleted."""
    return not any(shiboken6.isValid(item) for item in items)


def ink(image: QImage, item: QQuickItem) -> int:
    """How many pixels of ``item``'s area in ``image``, a grab of its window, differ from the
    one at the area's top left corner."""
    ratio = image.devicePixelRatio()
    area = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
    left, top = round(area.left() * ratio), round(area.top() * ratio)
    right, bottom = round(area.right() * ratio), round(area.bottom() * ratio)
    corner = image.pixel(left, top)
    return sum(image.pixel(x, y) != corner for x in range(left, right) for y in range(top, bottom))


CREATED = QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated
DESTROYED = QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed
# Windows keeps its native window, whose frame is set up once (desktop/titlebar.py).
GIVES_UP_NATIVE = sys.platform != "win32"


def test_hidden_the_window_lets_its_pages_go_and_shows_them_as_they_were(
    qtbot: QtBot, loaded: Loaded
) -> None:
    view_models, window = loaded.view_models, loaded.window
    shell, proxies = view_models.shell, view_models.proxies
    in_the_tray(loaded)
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))

    # What the user leaves on the pages: a group, the import form half filled in, a tab.
    shell.setPage("proxies")
    qtbot.waitUntil(lambda: proxies.property("currentGroup") != "")
    group = next(
        row.name
        for row in proxies.property("groups").rows
        if not row.header and row.name != proxies.property("currentGroup")
    )
    proxies.selectGroup(group)
    shell.setPage("profiles")
    QMetaObject.invokeMethod(page(loaded, "ProfilesPage"), "openImport")
    loaded.type(qtbot, LINK)
    interval = loaded.item(lambda item: kind(item) == "AutoUpdateField" and item.isVisible())
    interval.setProperty("intervalText", "6")
    shell.setPage("routing")
    routing = page(loaded, "RoutingPage")
    routing.setProperty("tab", "groups")

    surfaces = Surfaces(window)
    window.close()
    assert not window.isVisible()
    assert pages(window) == PAGES  # for a moment
    qtbot.waitUntil(lambda: not window.property("holdsPages"), timeout=5000)
    assert pages(window) == set()
    assert not shiboken6.isValid(routing)  # deleted, not just hidden
    assert surfaces.events == ([DESTROYED] if GIVES_UP_NATIVE else [])

    view_models.desktop.showWindow()
    # The pages are there for the window's first frame.
    assert window.isVisible()
    assert pages(window) == PAGES
    assert shell.property("currentPage") == "routing"
    assert page(loaded, "RoutingPage").property("tab") == "groups"
    assert surfaces.events == ([DESTROYED, CREATED] if GIVES_UP_NATIVE else [])
    shell.setPage("profiles")
    assert loaded.item(lambda item: item.property("text") == LINK)
    restored_interval = loaded.item(
        lambda item: kind(item) == "AutoUpdateField" and item.isVisible()
    )
    assert restored_interval.property("intervalText") == "6"
    shell.setPage("proxies")
    assert proxies.property("currentGroup") == group
    assert loaded.warnings == []


def test_a_dialog_left_open_keeps_its_page(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    in_the_tray(loaded)
    loaded.view_models.shell.setPage("profiles")
    profiles = page(loaded, "ProfilesPage")
    dialog = next(
        child
        for child in profiles.findChildren(QObject)
        if child.metaObject().className().startswith("ConfirmDialog_")
    )
    QMetaObject.invokeMethod(dialog, "open")
    qtbot.waitUntil(lambda: dialog.property("opened"))

    drawn = shapes(window.contentItem())
    (chart,) = [item for item in every_item(window.contentItem()) if kind(item) == "TrafficChart"]
    chart_drawn = shapes(chart)

    window.close()
    release(loaded)
    assert window.property("holdsPages")
    assert shiboken6.isValid(profiles)
    # The pages it keeps draw their shapes anew too, the overview's traffic lines among them.
    assert renewed(drawn, shapes(window.contentItem()))
    assert renewed(chart_drawn, shapes(chart))
    loaded.view_models.desktop.showWindow()
    assert dialog.property("opened")

    QMetaObject.invokeMethod(dialog, "close")
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    window.close()
    release(loaded)
    assert not window.property("holdsPages")
    assert loaded.warnings == []


@pytest.fixture(params=[MAC, WINDOWS])
def style(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """The window's title bar in this style, whatever system the tests run on, as in
    test_titlebar.py. Request it before ``loaded``."""
    monkeypatch.setattr("solaris.viewmodels.desktop.titlebar_style", lambda _: request.param)
    return request.param


@pytest.mark.usefixtures("screen_reader")
def test_the_sidebar_and_the_caption_buttons_draw_their_shapes_anew(
    qtbot: QtBot, style: str, loaded: Loaded
) -> None:
    """Issue #45: once the window gave its graphics back, Qt Quick's curve renderer drew nothing
    with the shapes the window keeps, the sidebar's mark and tabs and Windows' caption buttons.
    The window makes them again. The offscreen platform draws with the software scene graph,
    which would draw the old shapes too: here they must be new ones, and draw."""
    view_models, window = loaded.view_models, loaded.window
    desktop = view_models.desktop
    in_the_tray(loaded)
    desktop._windows = style == WINDOWS  # which keeps its native window (desktop/memory.py)
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))  # every page's tab
    sidebar = loaded.item(lambda item: kind(item) == "Sidebar")
    (buttons,) = [
        item for item in every_item(window.contentItem()) if kind(item) == "CaptionButtons"
    ]
    icons = [item for item in every_item(sidebar) if kind(item) == "Icon"]
    assert len(icons) == 8  # the mark and seven tabs
    if style == WINDOWS:
        icons += buttons.childItems()
    tabs = [item for item in every_item(sidebar) if kind(item) == "NavItem"]

    def told() -> list[tuple[Accessible, int]]:
        """What a screen reader is told of each tab, and how many parts it reads in it."""
        return [
            (accessible(tab), QAccessible.queryAccessibleInterface(tab).childCount())
            for tab in tabs
        ]

    before_told = told()
    tabs[1].forceActiveFocus()
    surfaces = Surfaces(window)

    for _ in range(3):
        before = shapes(sidebar) + shapes(buttons)
        surfaces.events.clear()
        window.close()
        release(loaded)
        assert renewed(before, shapes(sidebar) + shapes(buttons))
        qtbot.waitUntil(partial(gone, before))  # once the event loop deletes them
        assert surfaces.events == ([] if style == WINDOWS else [DESTROYED])

        desktop.showWindow()
        image = window.grabWindow()
        assert [ink(image, icon) > 0 for icon in icons] == [True] * len(icons)
        assert told() == before_told
        assert tabs[1].hasFocus()
    assert loaded.warnings == []


def test_hidden_and_let_go_the_window_still_asks_and_opens_links(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    view_models, window = loaded.view_models, loaded.window
    desktop, overview = view_models.desktop, view_models.overview
    host = view_models.host
    assert isinstance(host, SimulatedHost)
    in_the_tray(loaded)
    qtbot.waitUntil(lambda: overview.property("connected"))

    def hide() -> None:
        window.close()
        release(loaded)
        assert pages(window) == set()

    # An install link: the import form, filled in.
    hide()
    view_models.open_link("solaris://install-config?url=https%3A%2F%2Fsub.example.com%2Fa")
    assert window.isVisible()
    assert view_models.shell.property("currentPage") == "profiles"
    assert loaded.item(lambda item: item.property("text") == LINK)

    # The tray's 退出 Solaris, when the tunnel does not go down: the window asks.
    async def unresponsive() -> None:
        raise HostError("core_unresponsive", "the core did not answer")

    monkeypatch.setattr(host, "stop", unresponsive)
    hide()
    with qtbot.waitSignal(desktop.quitFailed):
        desktop.quit()
    qtbot.waitUntil(window.isVisible)
    qtbot.waitUntil(lambda: loaded.focused_text() == "重试")
    assert "ProfilesPage" in pages(window)
    loaded.key(qtbot, Qt.Key.Key_Escape)  # stay
    assert loaded.warnings == []


def test_a_window_brought_straight_back_keeps_its_pages(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    in_the_tray(loaded)
    loaded.view_models.shell.setPage("settings")
    settings = page(loaded, "SettingsPage")
    window.close()
    loaded.view_models.desktop.showWindow()
    qtbot.wait(2500)  # past the moment the window would have let them go
    assert window.property("holdsPages")
    assert shiboken6.isValid(settings)


@pytest.fixture
def at_login(qtbot: QtBot, tmp_path: Path, appearance: Appearance) -> Iterator[Loaded]:
    """The window as Solaris opens at login: hidden in the tray."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language("zh"):
        view_models = compose(
            SimulatedHost(),
            store,
            SimulatedFetcher(),
            appearance=appearance,
            updater=offline_updater(tmp_path),
            tray_available=True,
            start_hidden=True,
        )
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


def test_opened_at_login_the_window_loads_its_pages_as_it_first_shows(
    qtbot: QtBot, at_login: Loaded
) -> None:
    window = at_login.window
    qtbot.wait(200)
    assert not window.isVisible()
    assert pages(window) == set()
    at_login.view_models.desktop.showWindow()
    assert window.isVisible()
    assert pages(window) == PAGES
    assert at_login.warnings == []
