"""The main window's own title bar on each platform (DESIGN.md "顶部区域", docs/ui.md §8).

The window hides the system title bar and runs its content up to the top edge with Qt's
ExpandedClientAreaHint and NoTitleBarBackgroundHint; Main.qml lays out the 48px title band and
moves the window from its empty parts. This module covers what Qt leaves to the platform:

- macOS keeps the traffic lights, which the system draws and places. Qt leaves the window
  title showing in the middle of the band, so it is hidden (``titleVisibility``) through
  the Objective-C runtime; screen readers and the Window menu still have it. A double-click on
  the band does what "Double-click a window's title bar to" in the system settings says.
- Windows: with CustomizeWindowHint and no button hints Qt draws nothing in the band, and the
  system keeps the frame, its shadow and the resize borders; Main.qml draws the three caption
  buttons. WindowsFrame adds the minimize and maximize boxes that Qt leaves out of the window
  style without the button hints (the taskbar minimizes the window, Snap and the system menu
  offer both) and answers the hit test itself, so that the maximize button is the system's:
  hovering it opens Snap Layouts. Alt+Space opens the system menu.

viewmodels/titlebar.py hands this to Main.qml; on other Qt platforms, such as the tests'
offscreen one, the window gets the layout without these calls.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QPointF, QRectF, Qt

MAC = "mac"
WINDOWS = "windows"

# What a double-click on the title band does.
ZOOM = "zoom"
MINIMIZE = "minimize"
NONE = "none"


def titlebar_style(platform: str = sys.platform) -> str:
    """The title band follows macOS on macOS and Windows everywhere else."""
    return MAC if platform == "darwin" else WINDOWS


def window_flags(style: str) -> Qt.WindowType:
    flags = (
        Qt.WindowType.Window
        | Qt.WindowType.ExpandedClientAreaHint
        | Qt.WindowType.NoTitleBarBackgroundHint
    )
    if style == WINDOWS:
        # No button hints, so Qt draws no title bar of its own; the system menu stays.
        flags |= Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowSystemMenuHint
    return flags


def double_click_action(preference: str | None) -> str:
    """macOS's AppleActionOnDoubleClick: "Minimize", "None", or one that zooms ("Maximize",
    the default, and macOS 15's "Fill")."""
    return {"Minimize": MINIMIZE, "None": NONE}.get(preference or "", ZOOM)


# -- macOS -----------------------------------------------------------------------------------
NS_WINDOW_TITLE_HIDDEN = 1


class ObjC:
    """objc_msgSend through ctypes, as desktop/login.py does, without PyObjC."""

    def __init__(self) -> None:
        self._objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        for name in ("objc_getClass", "sel_registerName"):
            function = getattr(self._objc, name)
            function.restype, function.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        self._objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
        self._objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
        self._send_address = ctypes.cast(self._objc.objc_msgSend, ctypes.c_void_p).value or 0

    def send(
        self, restype: Any, receiver: int | None, selector: bytes, *args: Any, argtypes: tuple = ()
    ) -> Any:
        """objc_msgSend, cast to the method's own signature (arm64 needs the exact one)."""
        function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
            self._send_address
        )
        return function(receiver, self._objc.sel_registerName(selector), *args)

    def cls(self, name: bytes) -> int:
        return self._objc.objc_getClass(name)

    @contextmanager
    def pool(self) -> Iterator[None]:
        pool = self._objc.objc_autoreleasePoolPush()
        try:
            yield
        finally:
            self._objc.objc_autoreleasePoolPop(pool)


def hide_mac_title(view: int) -> None:
    """``view`` is the QWindow's NSView (its winId)."""
    objc = ObjC()
    with objc.pool():
        window = objc.send(ctypes.c_void_p, view, b"window")
        objc.send(
            None, window, b"setTitleVisibility:", NS_WINDOW_TITLE_HIDDEN, argtypes=(ctypes.c_long,)
        )


def mac_double_click_preference() -> str | None:
    objc = ObjC()
    with objc.pool():
        defaults = objc.send(ctypes.c_void_p, objc.cls(b"NSUserDefaults"), b"standardUserDefaults")
        key = objc.send(
            ctypes.c_void_p,
            objc.cls(b"NSString"),
            b"stringWithUTF8String:",
            b"AppleActionOnDoubleClick",
            argtypes=(ctypes.c_char_p,),
        )
        value = objc.send(
            ctypes.c_void_p, defaults, b"stringForKey:", key, argtypes=(ctypes.c_void_p,)
        )
        if not value:
            return None
        return objc.send(ctypes.c_char_p, value, b"UTF8String").decode()


# -- Windows ---------------------------------------------------------------------------------
NATIVE_EVENT = b"windows_generic_MSG"
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_SYSKEYDOWN = 0x0104
WM_SYSCOMMAND = 0x0112
WM_MOUSEMOVE = 0x0200
WM_NCMOUSELEAVE = 0x02A2
SC_KEYMENU = 0xF100
VK_SPACE = 0x20
ALT_DOWN = 1 << 29  # WM_SYSKEYDOWN's lParam: the key came with Alt
HTCLIENT = 1
HTMAXBUTTON = 9
HTTOP = 12
GWL_STYLE = -16
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
TME_LEAVE = 0x00000002
TME_NONCLIENT = 0x00000010
SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92


class MSG(ctypes.Structure):
    _fields_ = (
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
    )


class NativeWindow(Protocol):
    """The part of QWindow that WindowsFrame uses."""

    def winId(self) -> int: ...  # noqa: N802 - Qt's name
    def devicePixelRatio(self) -> float: ...  # noqa: N802 - Qt's name


class MaximizeButton(Protocol):
    """Where Main.qml draws the maximize button and how it shows (the TitleBar ViewModel)."""

    @property
    def maximize_area(self) -> QRectF: ...  # in the window's coordinates
    @property
    def maximize_pressed(self) -> bool: ...
    def set_maximize_state(self, *, hovered: bool, pressed: bool) -> None: ...
    def maximize_clicked(self) -> None: ...


class Win32(Protocol):
    """The calls WindowsFrame makes; tests pass a stand-in."""

    def default_hit_test(self, hwnd: int, wparam: int, lparam: int) -> int: ...
    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]: ...
    def zoomed(self, hwnd: int) -> bool: ...
    def resize_border(self, hwnd: int) -> int: ...
    def track_leave(self, hwnd: int) -> None: ...
    def open_system_menu(self, hwnd: int) -> None: ...
    def add_style(self, hwnd: int, bits: int) -> None: ...


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


class WindowsFrame(QAbstractNativeEventFilter):
    """Answers Windows for the main window's title band (see the module's docstring).

    The hit test puts the top resize border and the maximize button in the non-client area and
    leaves the rest to Qt's client area, where the band's own handlers move the window. The
    maximize button's hover, press and click then come as non-client messages: they go to
    Main.qml's button, and the clicks are not passed on, so that the system does not draw a
    maximize button of its own."""

    def __init__(self, window: NativeWindow, button: MaximizeButton, win32: Win32) -> None:
        super().__init__()
        self._window = window
        self._button = button
        self._win32 = win32
        self._hwnd = int(window.winId())
        self._tracking = False

    def install(self) -> None:
        self._win32.add_style(self._hwnd, WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type: Any, message: Any) -> tuple[bool, int]:  # noqa: N802 - Qt's name
        if bytes(event_type) != NATIVE_EVENT or not message:
            return False, 0
        msg = MSG.from_address(int(message))
        if (msg.hwnd or 0) != self._hwnd:
            return False, 0
        result = self.handle(msg.message, msg.wParam, msg.lParam)
        return (False, 0) if result is None else (True, result)

    def handle(self, message: int, wparam: int, lparam: int) -> int | None:
        """The result for ``message``, or None to pass it on to Qt."""
        on_maximize = wparam == HTMAXBUTTON
        if message == WM_NCHITTEST:
            return self.hit_test(wparam, lparam)
        if message == WM_NCMOUSEMOVE:
            self._button.set_maximize_state(
                hovered=on_maximize, pressed=on_maximize and self._button.maximize_pressed
            )
            if on_maximize and not self._tracking:
                self._win32.track_leave(self._hwnd)  # for WM_NCMOUSELEAVE
                self._tracking = True
            return None
        if message in (WM_NCMOUSELEAVE, WM_MOUSEMOVE):  # off the button, or back in the client area
            if message == WM_NCMOUSELEAVE:
                self._tracking = False
            self._button.set_maximize_state(hovered=False, pressed=False)
            return None
        if message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and on_maximize:
            self._button.set_maximize_state(hovered=True, pressed=True)
            return 0
        if message == WM_NCLBUTTONUP and on_maximize:
            clicked = self._button.maximize_pressed
            self._button.set_maximize_state(hovered=True, pressed=False)
            if clicked:
                self._button.maximize_clicked()
            return 0
        if message == WM_SYSKEYDOWN and wparam == VK_SPACE and lparam & ALT_DOWN:
            # Qt would open the system menu itself, with the entries its window flags allow.
            self._win32.open_system_menu(self._hwnd)
            return 0
        return None

    def hit_test(self, wparam: int, lparam: int) -> int:
        code = self._win32.default_hit_test(self._hwnd, wparam, lparam)
        if code != HTCLIENT:
            return code  # the frame's sides and bottom
        x, y = self._win32.screen_to_client(self._hwnd, _signed16(lparam), _signed16(lparam >> 16))
        if y < self._win32.resize_border(self._hwnd) and not self._win32.zoomed(self._hwnd):
            return HTTOP  # the client area reaches the top edge, so the border is in it
        ratio = self._window.devicePixelRatio()
        if self._button.maximize_area.contains(QPointF(x / ratio, y / ratio)):
            return HTMAXBUTTON
        return HTCLIENT


HWND = ctypes.c_void_p


class POINT(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTrack", ctypes.c_void_p),
        ("dwHoverTime", ctypes.c_uint32),
    )


def _user32() -> ctypes.CDLL:
    if sys.platform != "win32":
        raise OSError("user32 is Windows only")
    return ctypes.WinDLL("user32")


def _bind(library: ctypes.CDLL, name: str, restype: Any, *argtypes: Any) -> Any:
    function = getattr(library, name)
    function.restype, function.argtypes = restype, list(argtypes)
    return function


class User32:
    """Win32 for WindowsFrame, through ctypes."""

    def __init__(self) -> None:
        user32 = _user32()
        hwnd, uint, wparam, lparam = HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        self._def_window_proc = _bind(user32, "DefWindowProcW", lparam, hwnd, uint, wparam, lparam)
        self._screen_to_client = _bind(
            user32, "ScreenToClient", ctypes.c_int, hwnd, ctypes.POINTER(POINT)
        )
        self._is_zoomed = _bind(user32, "IsZoomed", ctypes.c_int, hwnd)
        self._dpi = _bind(user32, "GetDpiForWindow", uint, hwnd)
        self._metric = _bind(user32, "GetSystemMetricsForDpi", ctypes.c_int, ctypes.c_int, uint)
        self._track = _bind(
            user32, "TrackMouseEvent", ctypes.c_int, ctypes.POINTER(TRACKMOUSEEVENT)
        )
        self._post = _bind(user32, "PostMessageW", ctypes.c_int, hwnd, uint, wparam, lparam)
        self._get_long = _bind(user32, "GetWindowLongPtrW", lparam, hwnd, ctypes.c_int)
        self._set_long = _bind(user32, "SetWindowLongPtrW", lparam, hwnd, ctypes.c_int, lparam)

    def default_hit_test(self, hwnd: int, wparam: int, lparam: int) -> int:
        return self._def_window_proc(hwnd, WM_NCHITTEST, wparam, lparam)

    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        point = POINT(x, y)
        self._screen_to_client(hwnd, ctypes.byref(point))
        return point.x, point.y

    def zoomed(self, hwnd: int) -> bool:
        return bool(self._is_zoomed(hwnd))

    def resize_border(self, hwnd: int) -> int:
        dpi = self._dpi(hwnd)
        return self._metric(SM_CXSIZEFRAME, dpi) + self._metric(SM_CXPADDEDBORDER, dpi)

    def track_leave(self, hwnd: int) -> None:
        event = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE | TME_NONCLIENT, hwnd, 0)
        self._track(ctypes.byref(event))

    def open_system_menu(self, hwnd: int) -> None:
        self._post(hwnd, WM_SYSCOMMAND, SC_KEYMENU, VK_SPACE)

    def add_style(self, hwnd: int, bits: int) -> None:
        self._set_long(hwnd, GWL_STYLE, self._get_long(hwnd, GWL_STYLE) | bits)
