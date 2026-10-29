"""Desktop ViewModel: the tray icon, what closing the window does, and requests to show it.

Closing the window or quitting the UI never touches the tunnel; the service or the system
extension keeps it running (docs/architecture.md §1, §7.6).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.desktop.tray import taskbar_is_light, tray_icon
from mihomo_desktop.models.settings import Settings, SettingsStore

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

# Windows reports no event when only the taskbar's theme changes.
TASKBAR_POLL_MS = 10_000


def _url(path: object) -> str:
    return QUrl.fromLocalFile(str(path)).toString()


@QmlElement
@QmlUncreatable("DesktopViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class DesktopViewModel(QObject):
    trayIconChanged = Signal()
    closeActionChanged = Signal()
    showWindowRequested = Signal()  # another UI started, or the tray asked for the window
    reopened = Signal()  # macOS: the Dock icon was clicked; show the window if it is hidden
    quitRequested = Signal()  # leave the UI; the tunnel keeps running

    def __init__(
        self,
        settings: SettingsStore,
        *,
        tray_available: bool,
        start_hidden: bool = False,
        platform: str = sys.platform,
        parent: QObject | None = None,
    ) -> None:
        """``tray_available``: the Qt platform has a native tray. ``start_hidden``: the UI
        was opened at login and starts in the tray."""
        super().__init__(parent)
        self._settings = settings
        settings.subscribe(self._settings_changed)
        self._tray = tray_available
        self._start_hidden = start_hidden and tray_available
        self._mac = platform == "darwin"
        self._windows = platform == "win32"
        self._light_taskbar = taskbar_is_light()
        self._timer = QTimer(self)
        self._timer.setInterval(TASKBAR_POLL_MS)
        self._timer.timeout.connect(self.refreshTaskbar)
        app = QGuiApplication.instance()
        if self._mac and isinstance(app, QGuiApplication):
            # Qt reports every Dock click as the application becoming active.
            app.applicationStateChanged.connect(self._state_changed)

    def start(self) -> None:
        if self._tray and self._windows:
            self._timer.start()
            QGuiApplication.styleHints().colorSchemeChanged.connect(self.refreshTaskbar)

    def stop(self) -> None:
        self._timer.stop()

    def _state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.reopened.emit()

    # -- tray --------------------------------------------------------------------------------
    @Property(bool, constant=True)
    def trayAvailable(self) -> bool:
        return self._tray

    @Property(bool, constant=True)
    def startHidden(self) -> bool:
        return self._start_hidden

    @Property(bool, constant=True)
    def trayIconMask(self) -> bool:
        """macOS menu bar icons are template images."""
        return self._mac

    @Property(bool, constant=True)
    def showOnTrayClick(self) -> bool:
        """Windows opens the window on a click; macOS opens the menu."""
        return not self._mac

    def _icon(self, connected: bool) -> str:
        path = tray_icon(template=self._mac, light_taskbar=self._light_taskbar, connected=connected)
        return _url(path)

    @Property(str, notify=trayIconChanged)
    def trayIconOn(self) -> str:
        return self._icon(True)

    @Property(str, notify=trayIconChanged)
    def trayIconOff(self) -> str:
        return self._icon(False)

    @Slot()
    def refreshTaskbar(self) -> None:
        light = taskbar_is_light()
        if light != self._light_taskbar:
            self._light_taskbar = light
            self.trayIconChanged.emit()

    # -- window ------------------------------------------------------------------------------
    @Property(bool, notify=closeActionChanged)
    def hideOnClose(self) -> bool:
        """Closing the window leaves the UI in the tray; without a tray it quits the UI."""
        return self._tray and self._settings.current.close_action == "tray"

    def _settings_changed(self, before: Settings, after: Settings) -> None:
        if before.close_action != after.close_action:
            self.closeActionChanged.emit()

    @Slot()
    def showWindow(self) -> None:
        self.showWindowRequested.emit()

    @Slot()
    def quit(self) -> None:
        self.quitRequested.emit()
