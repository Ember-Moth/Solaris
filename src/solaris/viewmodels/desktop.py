"""Desktop ViewModel: the tray icon, the window's title bar, what closing the window does,
requests to show it, and quitting.

Closing the window into the tray leaves the tunnel running in the service or the system
extension; on macOS Solaris then leaves the Dock and stays in the menu bar (desktop/dock.py).
Quitting Solaris disconnects first (D41); only the installer's ``--quit`` and the system's
quit at the end of the session leave the tunnel as it is (docs/ui.md §8).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.desktop.dock import ACCESSORY, REGULAR, Application, MacApplication
from solaris.desktop.titlebar import titlebar_style
from solaris.desktop.tray import taskbar_is_light, tray_icon
from solaris.models.settings import Settings, SettingsStore
from solaris.viewmodels.feedback import failure_text
from solaris.viewmodels.titlebar import TitleBar

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

# Windows reports no event when only the taskbar's theme changes.
TASKBAR_POLL_MS = 10_000
# How long quitting waits for the tunnel to go down before it asks what to do (D41).
QUIT_DISCONNECT_TIMEOUT_S = 10.0

# OverviewViewModel.disconnect_to_quit: (timeout, done, failed) -> whether it disconnects.
Disconnect = Callable[[float, Callable[[], None], Callable[[BaseException], None]], bool]


def _url(path: object) -> str:
    return QUrl.fromLocalFile(str(path)).toString()


@QmlElement
@QmlUncreatable("DesktopViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class DesktopViewModel(QObject):
    trayIconChanged = Signal()
    closeActionChanged = Signal()
    showWindowRequested = Signal()  # another UI started, or the tray asked for the window
    reopened = Signal()  # macOS: opened again (the Dock, the Finder); show a hidden window
    quitRequested = Signal()  # leave the event loop now
    quitChanged = Signal()
    quitFailed = Signal()  # the disconnect before quitting failed; ask what to do

    def __init__(
        self,
        settings: SettingsStore,
        *,
        tray_available: bool,
        start_hidden: bool = False,
        platform: str = sys.platform,
        disconnect: Disconnect | None = None,
        application: Application | None = None,
        parent: QObject | None = None,
    ) -> None:
        """``tray_available``: the Qt platform has a native tray. ``start_hidden``: the UI
        was opened at login and starts in the tray. ``disconnect`` takes this user's tunnel
        down before Solaris quits; without it quitting leaves at once. ``application``: on
        macOS, NSApp's activation policy; by default the real one on the ``cocoa`` platform
        and none elsewhere."""
        super().__init__(parent)
        self._settings = settings
        self._disconnect = disconnect
        self._quitting = False  # disconnecting before quitting
        self._quit_failure: BaseException | None = None  # while the prompt asks
        self._left = False
        settings.subscribe(self._settings_changed)
        self._tray = tray_available
        self._start_hidden = start_hidden and tray_available
        self._mac = platform == "darwin"
        self._windows = platform == "win32"
        if application is None and self._mac and QGuiApplication.platformName() == "cocoa":
            application = MacApplication()
        self._application = application if self._mac else None
        self.title_bar = TitleBar(titlebar_style(platform), self)
        self._light_taskbar = taskbar_is_light()
        self._timer = QTimer(self)
        self._timer.setInterval(TASKBAR_POLL_MS)
        self._timer.timeout.connect(self.refreshTaskbar)
        app = QGuiApplication.instance()
        if self._mac and isinstance(app, QGuiApplication):
            # Qt reports every Dock click as the application becoming active.
            app.applicationStateChanged.connect(self._state_changed)

    def start(self) -> None:
        if self._start_hidden:
            self._dock(shown=False)
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
    @Property(TitleBar, constant=True)
    def titleBar(self) -> TitleBar:
        return self.title_bar

    @Property(bool, notify=closeActionChanged)
    def hideOnClose(self) -> bool:
        """Closing the window leaves the UI in the tray, the tunnel running; otherwise, and
        without a tray, it quits Solaris."""
        return self._tray and self._settings.current.close_action == "tray"

    def _settings_changed(self, before: Settings, after: Settings) -> None:
        if before.close_action != after.close_action:
            self.closeActionChanged.emit()

    @Slot()
    def showWindow(self) -> None:
        self.showWindowRequested.emit()

    @Slot(bool)
    def setWindowShown(self, shown: bool) -> None:
        """Main.qml: the window was shown or hidden, whichever way: the menu bar's 打开主窗口,
        a second launch, an install link, the dialogs that bring it back, or closing it."""
        self._dock(shown=shown)

    def _dock(self, *, shown: bool) -> None:
        """macOS: in the Dock and ⌘Tab while the window shows; while it hides, in the menu bar
        only. A minimized window still shows, in the Dock. Showing it activates Solaris, so
        that the menu bar is Solaris's, not the app's in front. Without a tray, hiding the
        window quits, and Solaris keeps its Dock icon until it is gone."""
        if self._application is None or not self._tray:
            return
        if shown:
            self._application.set_policy(REGULAR)
            self._application.activate()
        else:
            self._application.set_policy(ACCESSORY)

    # -- quitting ----------------------------------------------------------------------------
    @Property(bool, notify=quitChanged)
    def quitting(self) -> bool:
        return self._quitting

    @Property(str, notify=quitChanged)
    def quitMessage(self) -> str:
        if self._quit_failure is None:
            return ""
        reason = failure_text(self._quit_failure)
        return self.tr("{reason}。仍然退出时，隧道可能仍在运行。").format(reason=reason)

    @Slot()
    def quit(self) -> None:
        """Quit Solaris (D41): the tray's 退出 Solaris, ⌘Q and the Dock's Quit, and closing
        the window when that quits. This user's tunnel goes down first, as with 断开, and the
        UI leaves once it is down; when it does not go down in time, the window asks whether
        to try again or to quit anyway."""
        if self._quitting or self._left:
            return
        if self._quit_failure is not None:
            self.showWindowRequested.emit()  # the prompt is still asking
            return
        disconnect = self._disconnect
        if disconnect is None or not disconnect(
            QUIT_DISCONNECT_TIMEOUT_S, self.leave, self._quit_failed
        ):
            self.leave()
            return
        self._quitting = True
        self.quitChanged.emit()

    @Slot()
    def retryQuit(self) -> None:
        self.cancelQuit()
        self.quit()

    @Slot()
    def cancelQuit(self) -> None:
        """The prompt was dismissed: Solaris keeps running."""
        if self._quit_failure is not None:
            self._quit_failure = None
            self.quitChanged.emit()

    @Slot()
    def leave(self) -> None:
        """Leave the event loop now and the tunnel as it is: once quitting disconnected it,
        when the user quits anyway, and for the installer's ``--quit``."""
        if self._left:
            return
        self._left = True
        self._quitting = False
        self._quit_failure = None
        self.quitChanged.emit()
        self.quitRequested.emit()

    def _quit_failed(self, error: BaseException) -> None:
        if self._left:
            return
        self._quitting = False
        self._quit_failure = error
        self.quitChanged.emit()
        self.quitFailed.emit()
