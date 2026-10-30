"""The window's own title band (DESIGN.md "顶部区域", docs/ui.md §8).

The layout is checked in both platforms' styles on the offscreen platform. The Windows hit test
runs against a stand-in for user32; only a Windows machine shows what the system does with it.
"""

import ctypes

import pytest
from PySide6.QtCore import QCoreApplication, QPoint, QRectF, Qt
from PySide6.QtGui import QAccessible, QGuiApplication, QWindow
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from solaris.desktop import titlebar
from solaris.desktop.titlebar import (
    HTCLIENT,
    HTMAXBUTTON,
    HTTOP,
    MAC,
    MINIMIZE,
    MSG,
    NONE,
    VK_SPACE,
    WINDOWS,
    WM_MOUSEMOVE,
    WM_NCHITTEST,
    WM_NCLBUTTONDOWN,
    WM_NCLBUTTONUP,
    WM_NCMOUSELEAVE,
    WM_NCMOUSEMOVE,
    WM_SYSKEYDOWN,
    WS_MAXIMIZEBOX,
    WS_MINIMIZEBOX,
    ZOOM,
    WindowsFrame,
    double_click_action,
    titlebar_style,
    window_flags,
)
from solaris.viewmodels.titlebar import TitleBar
from tests.support.window import Loaded, accessible, find_all, kind

# DESIGN.md's spacing tokens.
TITLEBAR_HEIGHT = 48
TITLEBAR_PAGE_TOP = 6
CAPTION_BUTTON_WIDTH = 46
SIDEBAR_TOP = {MAC: 52, WINDOWS: 9}
HWND = 0x1234
HTLEFT = 10
ALT = 1 << 29


def test_the_style_follows_the_system() -> None:
    assert titlebar_style("darwin") == MAC
    assert titlebar_style("win32") == WINDOWS


def test_only_windows_customizes_the_title_bar_away() -> None:
    expanded = Qt.WindowType.ExpandedClientAreaHint | Qt.WindowType.NoTitleBarBackgroundHint
    for style in (MAC, WINDOWS):
        assert window_flags(style) & expanded == expanded
    assert not window_flags(MAC) & Qt.WindowType.CustomizeWindowHint
    windows = window_flags(WINDOWS)
    assert windows & Qt.WindowType.CustomizeWindowHint
    assert windows & Qt.WindowType.WindowSystemMenuHint
    buttons = (
        Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowMinimizeButtonHint
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    assert not windows & buttons, "Qt would draw its own title bar"


@pytest.mark.parametrize(
    ("preference", "action"),
    [(None, ZOOM), ("Maximize", ZOOM), ("Fill", ZOOM), ("Minimize", MINIMIZE), ("None", NONE)],
)
def test_a_double_click_does_what_macos_is_set_to(preference: str | None, action: str) -> None:
    assert double_click_action(preference) == action


def test_off_macos_a_double_click_zooms(qapp: object) -> None:
    assert TitleBar(MAC).doubleClickAction() == ZOOM  # the offscreen platform
    assert TitleBar(WINDOWS).doubleClickAction() == ZOOM


# -- Windows: the hit test and the maximize button ----------------------------------------------
class FakeWin32:
    """user32 for a window at the screen's origin whose client area starts at (8, 0)."""

    def __init__(self) -> None:
        self.zoomed_ = False
        self.frame: dict[tuple[int, int], int] = {}  # screen points outside the client area
        self.tracked = 0
        self.menus = 0
        self.styles: list[int] = []

    def default_hit_test(self, hwnd: int, wparam: int, lparam: int) -> int:
        point = (titlebar._signed16(lparam), titlebar._signed16(lparam >> 16))
        return self.frame.get(point, HTCLIENT)

    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        return x - 8, y

    def zoomed(self, hwnd: int) -> bool:
        return self.zoomed_

    def resize_border(self, hwnd: int) -> int:
        return 8

    def track_leave(self, hwnd: int) -> None:
        self.tracked += 1

    def open_system_menu(self, hwnd: int) -> None:
        self.menus += 1

    def add_style(self, hwnd: int, bits: int) -> None:
        self.styles.append(bits)


class FakeWindow:
    def __init__(self, ratio: float) -> None:
        self.ratio = ratio

    def winId(self) -> int:  # noqa: N802
        return HWND

    def devicePixelRatio(self) -> float:  # noqa: N802
        return self.ratio


def point(x: int, y: int) -> int:
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


@pytest.fixture
def frame(qapp: object) -> tuple[WindowsFrame, TitleBar, FakeWin32]:
    """A 1280px window at 150 %, its maximize button where Main.qml puts it."""
    bar, win32 = TitleBar(WINDOWS), FakeWin32()
    bar.setProperty("maximizeArea", QRectF(1280 - 2 * 46, 0, 46, 48))
    return WindowsFrame(FakeWindow(1.5), bar, win32), bar, win32


def shows(bar: TitleBar) -> tuple[bool, bool]:
    """Whether the maximize button shows hovered, and pressed."""
    return bar.property("maximizeHovered"), bar.property("maximizePressed")


def test_the_hit_test_makes_the_maximize_button_the_systems(
    frame: tuple[WindowsFrame, TitleBar, FakeWin32],
) -> None:
    windows, _, win32 = frame
    left_of_button = 8 + int((1280 - 2 * 46 - 1) * 1.5)
    on_button = 8 + int((1280 - 2 * 46 + 10) * 1.5)
    assert windows.handle(WM_NCHITTEST, 0, point(on_button, 30)) == HTMAXBUTTON
    assert windows.handle(WM_NCHITTEST, 0, point(left_of_button, 30)) == HTCLIENT
    assert windows.handle(WM_NCHITTEST, 0, point(on_button, 48 * 2)) == HTCLIENT

    # The top resize border lies in the client area, except while the window is maximized.
    assert windows.handle(WM_NCHITTEST, 0, point(on_button, 3)) == HTTOP
    assert windows.handle(WM_NCHITTEST, 0, point(400, 3)) == HTTOP
    win32.zoomed_ = True
    assert windows.handle(WM_NCHITTEST, 0, point(on_button, 3)) == HTMAXBUTTON
    assert windows.handle(WM_NCHITTEST, 0, point(400, 3)) == HTCLIENT

    # The frame's sides and bottom stay the system's.
    win32.frame[(2, 300)] = HTLEFT
    assert windows.handle(WM_NCHITTEST, 0, point(2, 300)) == HTLEFT


def test_the_maximize_button_shows_and_takes_the_systems_clicks(
    qtbot: QtBot, frame: tuple[WindowsFrame, TitleBar, FakeWin32]
) -> None:
    windows, bar, win32 = frame
    assert windows.handle(WM_NCMOUSEMOVE, HTMAXBUTTON, 0) is None
    assert shows(bar) == (True, False)
    windows.handle(WM_NCMOUSEMOVE, HTMAXBUTTON, 0)
    assert win32.tracked == 1, "asks once for WM_NCMOUSELEAVE"

    # Down and up are answered here, so the system does not draw a button of its own.
    with qtbot.waitSignal(bar.maximizeClicked):
        assert windows.handle(WM_NCLBUTTONDOWN, HTMAXBUTTON, 0) == 0
        assert shows(bar) == (True, True)
        assert windows.handle(WM_NCLBUTTONUP, HTMAXBUTTON, 0) == 0
    assert shows(bar) == (True, False)

    assert windows.handle(WM_NCMOUSELEAVE, 0, 0) is None
    assert shows(bar) == (False, False)
    windows.handle(WM_NCMOUSEMOVE, HTMAXBUTTON, 0)
    assert win32.tracked == 2

    # A press that leaves the button is no click.
    windows.handle(WM_NCLBUTTONDOWN, HTMAXBUTTON, 0)
    assert windows.handle(WM_MOUSEMOVE, 0, 0) is None
    assert shows(bar) == (False, False)
    with qtbot.assertNotEmitted(bar.maximizeClicked):
        windows.handle(WM_NCLBUTTONUP, HTMAXBUTTON, 0)

    # Elsewhere the non-client clicks are the system's.
    assert windows.handle(WM_NCLBUTTONDOWN, HTLEFT, 0) is None


def test_alt_space_opens_the_system_menu(frame: tuple[WindowsFrame, TitleBar, FakeWin32]) -> None:
    windows, _, win32 = frame
    assert windows.handle(WM_SYSKEYDOWN, VK_SPACE, ALT) == 0
    assert win32.menus == 1
    assert windows.handle(WM_SYSKEYDOWN, 0x41, ALT) is None
    assert win32.menus == 1


def test_the_filter_answers_for_its_window_only(
    frame: tuple[WindowsFrame, TitleBar, FakeWin32],
) -> None:
    windows, _, win32 = frame
    on_button = point(8 + int((1280 - 60) * 1.5), 30)
    message = MSG(hwnd=HWND, message=WM_NCHITTEST, wParam=0, lParam=on_button)
    assert windows.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)) == (
        True,
        HTMAXBUTTON,
    )
    message.message = WM_MOUSEMOVE
    assert windows.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(message)) == (
        False,
        0,
    )
    other = MSG(hwnd=HWND + 1, message=WM_NCHITTEST, wParam=0, lParam=on_button)
    assert windows.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(other)) == (
        False,
        0,
    )

    assert windows.nativeEventFilter(b"mac_generic_NSEvent", ctypes.addressof(message)) == (
        False,
        0,
    )

    app = QCoreApplication.instance()
    assert app is not None
    windows.install()
    try:
        assert win32.styles == [WS_MINIMIZEBOX | WS_MAXIMIZEBOX]
    finally:
        app.removeNativeEventFilter(windows)


# -- the band in the window ---------------------------------------------------------------------
@pytest.fixture(params=[MAC, WINDOWS])
def style(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """The window in this style, whatever system the tests run on. Request it before
    ``loaded``."""
    monkeypatch.setattr("solaris.viewmodels.desktop.titlebar_style", lambda _: request.param)
    return request.param


def caption_buttons(loaded: Loaded) -> list[QQuickItem]:
    row = find_all(loaded.window.contentItem(), lambda i: kind(i) == "CaptionButtons")
    return [b for r in row for b in r.childItems() if b.isVisible()]


def scene_rect(item: QQuickItem) -> QRectF:
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def title_of(loaded: Loaded) -> QQuickItem:
    header = loaded.item(lambda i: kind(i) == "PageHeader")
    return loaded.item(
        lambda i: kind(i) == "UiText" and i.property("text") == header.property("title")
    )


def test_the_window_is_the_designs_size(style: str, loaded: Loaded) -> None:
    window = loaded.window
    assert (window.width(), window.height()) == (1280, 800)
    assert window.flags() == window_flags(style)


def test_the_band_lays_out_as_designed(qtbot: QtBot, style: str, loaded: Loaded) -> None:
    window = loaded.window
    qtbot.waitUntil(lambda: loaded.view_models.overview.property("connected"))
    # The page title is centred on the band's middle line.
    title = scene_rect(title_of(loaded))
    assert title.center().y() == pytest.approx(TITLEBAR_HEIGHT / 2, abs=1)
    header = scene_rect(loaded.item(lambda i: kind(i) == "PageHeader"))
    assert header.top() == TITLEBAR_PAGE_TOP

    sidebar = loaded.item(lambda i: kind(i) == "Sidebar")
    mark = loaded.item(lambda i: kind(i) == "Icon" and i.property("name") == "mark")
    brand = mark.parentItem().parentItem()  # the mark's tile, the app's name and what is in use
    assert scene_rect(brand).top() == SIDEBAR_TOP[style]
    assert scene_rect(sidebar).top() == 0

    buttons = caption_buttons(loaded)
    if style == MAC:
        assert buttons == []
        assert header.right() == window.width() - 32
        return
    # Windows: three buttons in the top right corner; the header keeps 138 + 8 px clear.
    assert [scene_rect(b) for b in buttons] == [
        QRectF(window.width() - (3 - i) * CAPTION_BUTTON_WIDTH, 0, 46, 48) for i in range(3)
    ]
    rule = header.bottom() - 1
    assert loaded.item(lambda i: kind(i) == "PageHeader").property("rowWidth") == (
        header.width() - (3 * 46 + 8 - 32)
    )
    assert rule > TITLEBAR_HEIGHT
    bar = loaded.view_models.desktop.title_bar
    assert bar.maximize_area == scene_rect(buttons[1])


@pytest.mark.usefixtures("screen_reader")
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("style", [WINDOWS], indirect=True)
def test_caption_buttons_are_named_and_reached_last(
    request: pytest.FixtureRequest, qtbot: QtBot, style: str, loaded: Loaded
) -> None:
    window = loaded.window
    buttons = caption_buttons(loaded)
    english = request.node.callspec.params["loaded"] == "en"
    names = ["Minimize", "Maximize", "Close"] if english else ["最小化", "最大化", "关闭"]
    assert [accessible(b).name for b in buttons] == names
    assert {accessible(b).role for b in buttons} == {QAccessible.Role.Button}

    # Tab reaches them after the page and goes on to the sidebar; each shows its ring.
    buttons[0].forceActiveFocus(Qt.FocusReason.TabFocusReason)
    for button in buttons[1:]:
        loaded.key(qtbot, Qt.Key.Key_Tab, wait=10)
        assert loaded.focused() is button
        assert loaded.focus_ring(button) is not None
    loaded.key(qtbot, Qt.Key.Key_Tab, wait=10)
    assert kind(loaded.focused()) == "NavItem"

    # A click leaves the focus where it was.
    focused = loaded.focused()
    with qtbot.waitSignal(loaded.view_models.desktop.quitRequested):
        loaded.click(qtbot, buttons[2])  # no tray in the tests, so closing quits
    assert loaded.focused() is focused

    loaded.click(qtbot, buttons[1])
    qtbot.waitUntil(lambda: window.visibility() == QWindow.Visibility.Maximized)
    assert accessible(buttons[1]).name == ("Restore" if english else "还原")
    window.showNormal()


def test_a_double_click_on_an_empty_part_of_the_band_zooms(
    qtbot: QtBot, style: str, loaded: Loaded
) -> None:
    window = loaded.window
    window.show()
    title = scene_rect(title_of(loaded))
    interval = QGuiApplication.styleHints().mouseDoubleClickInterval()
    # The sidebar's top, the header beside the title, and above the header.
    for spot in (QPoint(100, 20), QPoint(int(title.right()) + 300, 24), QPoint(260, 3)):
        QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, spot)
        qtbot.waitUntil(lambda: window.visibility() == QWindow.Visibility.Maximized)
        window.showNormal()
        qtbot.waitUntil(lambda: window.visibility() != QWindow.Visibility.Maximized)
        qtbot.wait(interval + 50)  # so that the next two clicks are not a third and fourth

    # A double-click on a control in the band is the control's.
    if style == WINDOWS:
        QTest.mouseDClick(
            window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            scene_rect(caption_buttons(loaded)[0]).center().toPoint(),
        )
        qtbot.wait(interval + 50)
        assert window.visibility() != QWindow.Visibility.Maximized
    assert loaded.warnings == []
