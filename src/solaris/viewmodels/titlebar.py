"""Title bar ViewModel: the main window's own title band (DESIGN.md "顶部区域", docs/ui.md §8).

Main.qml takes the window flags and the platform's style from here, and the components of the
band ask it what a double-click does. The platform calls are in desktop/titlebar.py; Main.qml
calls ``attach`` for them once it is complete.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QEvent, QObject, QRect, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication, QPlatformSurfaceEvent, QWindow
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.desktop.titlebar import (
    MAC,
    WINDOWS,
    ZOOM,
    User32,
    WindowsFrame,
    double_click_action,
    hide_mac_title,
    mac_double_click_preference,
    window_flags,
)

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlUncreatable("TitleBar is created in Python and reached through DesktopViewModel.titleBar.")  # pyright: ignore[reportCallIssue]
class TitleBar(QObject):
    maximizeAreaChanged = Signal()
    maximizeStateChanged = Signal()
    maximizeClicked = Signal()  # Windows: the maximize button was clicked, through the hit test

    def __init__(self, style: str, parent: QObject | None = None) -> None:
        """``style`` is ``mac`` or ``windows`` (desktop.titlebar.titlebar_style)."""
        super().__init__(parent)
        self._style = style
        self._maximize_area = QRectF()
        self._maximize_hovered = False
        self._maximize_pressed = False
        self._frame: WindowsFrame | None = None
        self._geometry: QRect | None = None  # macOS: the window's, as its native window went

    @Property(str, constant=True)
    def style(self) -> str:
        return self._style

    @Property(int, constant=True)
    def windowFlags(self) -> int:
        return window_flags(self._style).value

    @Slot(result=str)
    def doubleClickAction(self) -> str:
        """``zoom``, ``minimize`` or ``none``; read at each double-click, so a changed system
        setting applies at once."""
        if self._style == MAC and QGuiApplication.platformName() == "cocoa":
            return double_click_action(mac_double_click_preference())
        return ZOOM

    @Slot(QWindow)
    def attach(self, window: QWindow) -> None:
        """Do what Qt leaves to the platform for ``window``, the main window. This creates the
        native window if it is still hidden. On macOS the window gives up its native window
        while it hides (desktop/memory.py); the one Qt creates as it shows again is set up the
        same way. Windows keeps its native window."""
        platform = QGuiApplication.platformName()
        if self._style == MAC and platform == "cocoa":
            hide_mac_title(int(window.winId()))
            window.installEventFilter(self)
        elif self._style == WINDOWS and platform == "windows":
            self._frame = WindowsFrame(window, self, User32())
            self._frame.install()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """macOS: Qt creates the native window again with the frame of a window with a title
        bar, as it did the first time (Main.qml); the window gets back the place and the size
        it had, unless it was zoomed or full screen, which Qt restores itself."""
        if isinstance(watched, QWindow) and isinstance(event, QPlatformSurfaceEvent):
            surface = event.surfaceEventType()
            if surface == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed:
                normal = watched.windowState() == Qt.WindowState.WindowNoState
                self._geometry = watched.geometry() if normal else None
            elif surface == QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated:
                hide_mac_title(int(watched.winId()))
                if self._geometry is not None:
                    watched.setGeometry(self._geometry)
        return super().eventFilter(watched, event)

    # -- Windows: the maximize button, seen through the system's hit test ---------------------
    @property
    def maximize_area(self) -> QRectF:
        return self._maximize_area

    def _get_maximize_area(self) -> QRectF:
        return self._maximize_area

    def _set_maximize_area(self, area: QRectF) -> None:
        if area != self._maximize_area:
            self._maximize_area = QRectF(area)
            self.maximizeAreaChanged.emit()

    # Where the maximize button is, in the window's coordinates; Main.qml keeps it current.
    maximizeArea = Property(
        QRectF, _get_maximize_area, _set_maximize_area, notify=maximizeAreaChanged
    )

    @Property(bool, notify=maximizeStateChanged)
    def maximizeHovered(self) -> bool:
        """The pointer is on the maximize button. The system has the pointer there, so the
        button does not see the hover itself."""
        return self._maximize_hovered

    @Property(bool, notify=maximizeStateChanged)
    def maximizePressed(self) -> bool:
        return self._maximize_pressed

    @property
    def maximize_pressed(self) -> bool:
        return self._maximize_pressed

    def set_maximize_state(self, *, hovered: bool, pressed: bool) -> None:
        if (hovered, pressed) != (self._maximize_hovered, self._maximize_pressed):
            self._maximize_hovered, self._maximize_pressed = hovered, pressed
            self.maximizeStateChanged.emit()

    def maximize_clicked(self) -> None:
        self.maximizeClicked.emit()
