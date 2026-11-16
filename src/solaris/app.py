"""Composition root: builds models, wraps them in ViewModels and loads the QML view."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

from solaris import __version__
from solaris.desktop.associations import clear_stale_in_background
from solaris.desktop.dock import open_in_menu_bar
from solaris.desktop.instance import SingleInstance, ask_to_quit
from solaris.desktop.links import LinkReceiver, from_arguments
from solaris.desktop.login import BACKGROUND_ARG, LoginItem, default_login_item
from solaris.desktop.session import session_ending
from solaris.desktop.tray import tray_supported
from solaris.fonts import register_fonts
from solaris.host.base import TAKEOVER_NONE, TAKEOVER_SETTINGS, HostError, TunnelHost
from solaris.host.service import ServiceHost
from solaris.host.transport import Opener, Streams
from solaris.i18n import Translations, resolve_language
from solaris.models.backup_google import GoogleDriveProvider, configured_provider
from solaris.models.backup_icloud import bundled_provider
from solaris.models.credentials import KeyringCredentials
from solaris.models.database import DATABASE_NAME
from solaris.models.settings import DatabaseSettingsStore, SettingsStore, write_settings
from solaris.models.store import ProfileStore, default_data_dir
from solaris.models.subscription import Fetcher, UrlFetcher
from solaris.product import DISPLAY_NAME, PIPE_NAME
from solaris.update.updater import Updater
from solaris.viewmodels.appearance import Appearance, resolve_mode
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.text_editing import ClickAway

# Import registers the presentation helper for QML, including standalone component tests.
from solaris.viewmodels.text_editing import TextEditing as TextEditing

MAIN_QML = Path(__file__).resolve().parent / "ui" / "Main.qml"
DEV_SOCKET_ENV = "SOLARIS_DEV_SOCKET"
# Ask the UI already running to quit and wait for it, as the Windows installer does before
# it replaces the files (docs/ui.md §8); exits 0 once no UI runs. The tunnel stays as it is.
QUIT_ARG = "--quit"


class Application(QGuiApplication):
    """Qt reports ⌘Q, the Dock's Quit and the system's quit at the end of the session as a
    Quit event, and would first close the windows and give up when one refuses, as the main
    window does while it hides in the tray (Main.qml). The user's quit goes to ``user_quit``
    instead, which disconnects first (D41); the system's leaves the event loop at once and
    leaves the tunnel to the service (D5)."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self.user_quit: Callable[[], None] | None = None

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Quit:
            if self.user_quit is not None and not session_ending():
                self.user_quit()
            else:
                self.exit(0)
            return True
        return super().event(event)


def default_opener() -> Opener:
    """The installed service on Windows; elsewhere the development socket, if one is set."""
    if sys.platform == "win32":
        from solaris.host.transport import pipe_opener

        return pipe_opener(PIPE_NAME)
    from solaris.host.transport import unix_opener

    socket = os.environ.get(DEV_SOCKET_ENV)
    if socket:
        return unix_opener(socket)

    async def unavailable() -> Streams:
        raise HostError("service_unavailable", f"set {DEV_SOCKET_ENV} to a development socket")

    return unavailable


def default_host() -> TunnelHost:
    """The system extension in a packaged Solaris.app on macOS, unless a development socket is
    set (docs/architecture.md §6); otherwise the service over default_opener. Off Windows
    that is the development service, which takes over nothing (§11)."""
    if sys.platform == "darwin" and not os.environ.get(DEV_SOCKET_ENV):
        from solaris.host.extension import bundled_host

        host = bundled_host()
        if host is not None:
            return host
    takeover = TAKEOVER_SETTINGS if sys.platform == "win32" else TAKEOVER_NONE
    return ServiceHost(default_opener(), takeover=takeover)


def default_take_over(host: TunnelHost) -> Callable[[], Coroutine[Any, Any, None]] | None:
    """An administrator's take-over of another user's tunnel (D5b): only with the installed
    Windows service, whose executable runs as the take-over helper."""
    if sys.platform != "win32" or not isinstance(host, ServiceHost):
        return None
    from solaris.desktop import elevation

    if not elevation.is_administrator() or not elevation.service_executable().is_file():
        return None
    return partial(host.take_over, elevation.take_over)


def load_ui(engine: QQmlApplicationEngine, view_models: ViewModels) -> bool:
    register_fonts()
    engine.setInitialProperties(view_models.initial_properties())
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    for window in engine.rootObjects():
        if isinstance(window, QQuickWindow):
            ClickAway(window, engine)
    return bool(engine.rootObjects())


def switch_language(
    translations: Translations, engine: QQmlApplicationEngine, view_models: ViewModels
) -> None:
    """Show the running UI in the language the settings name (docs/ui.md §5)."""
    translations.apply(resolve_language(view_models.settings_store.current.language))
    engine.retranslate()
    view_models.retranslate()


def stored_appearance(settings: SettingsStore) -> str:
    """The saved appearance; empty for ``system``, so SOLARIS_APPEARANCE still applies."""
    mode = settings.current.appearance
    return "" if mode == "system" else mode


def main(
    host: TunnelHost | None = None,
    store: ProfileStore | None = None,
    fetcher: Fetcher | None = None,
    language: str = "",
    appearance: str = "",
    login_item: LoginItem | None = None,
    updater: Updater | None = None,
    google: GoogleDriveProvider | None = None,
) -> int:
    """``language`` is ``zh`` or ``en`` and wins over the settings at startup; empty follows
    the settings, then SOLARIS_LANG, then the system. ``appearance`` is ``system``,
    ``light`` or ``dark`` and likewise wins over the settings; empty follows the settings,
    then SOLARIS_APPEARANCE, then the system. The settings live in the profiles'
    database, and the lock that keeps one UI per user beside it; the lock is taken before
    the database is opened, so only one UI migrates it, and let go after the store is
    closed on exit. With ``--quit`` it asks the UI already running to quit instead."""
    directory = store.directory if store else default_data_dir()
    if QUIT_ARG in sys.argv[1:]:
        _core = QCoreApplication.instance() or QCoreApplication(sys.argv)
        return 0 if ask_to_quit(directory) else 1
    start_hidden = BACKGROUND_ARG in sys.argv[1:]
    if start_hidden:
        open_in_menu_bar()
    app = Application(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    # The bundle's icon covers the packaged macOS app; this one covers Windows' taskbar and
    # runs from source (scripts/gen_icons.py).
    brand = Path(__file__).resolve().parent / "ui" / "brand"
    app.setWindowIcon(
        QIcon(str(brand / ("app-icon-macos.svg" if sys.platform == "darwin" else "app-icon.svg")))
    )
    # Closing the window hides it in the tray or quits Solaris (DesktopViewModel).
    app.setQuitOnLastWindowClosed(False)
    # macOS sends install links as Apple Events, which can come before the window does.
    receiver = LinkReceiver(app)
    link = from_arguments(sys.argv[1:])
    instance = SingleInstance(directory)
    if not instance.claim(link):
        return 0  # the UI already running shows its window, or opens the link
    # Windows: a stale link registration in the user's own HKCU hides Solaris's (D45).
    clear_stale_in_background(app.platformName())
    if store is None:
        new_windows_install = sys.platform == "win32" and not (directory / DATABASE_NAME).exists()
        store = ProfileStore(directory)
        if new_windows_install:
            with store.database.transaction() as connection:
                write_settings(
                    connection, {"system_proxy": False}, store.database.clock(), keep=True
                )
    settings = DatabaseSettingsStore(store.database)
    translations = Translations(app)
    translations.apply(resolve_language(language or settings.current.language))
    host = host or default_host()
    view_models = compose(
        host,
        store,
        fetcher or UrlFetcher(),
        appearance=Appearance(resolve_mode(appearance or stored_appearance(settings))),
        settings=settings,
        login_item=login_item or default_login_item(),
        tray_available=tray_supported(app.platformName()),
        start_hidden=start_hidden,
        updater=updater,
        icloud=bundled_provider(),
        google=google or configured_provider(KeyringCredentials()),
        take_over=default_take_over(host),
    )
    engine = QQmlApplicationEngine()
    if not load_ui(engine, view_models):
        store.close()
        instance.release()
        return 1
    # Queued: the switch happens once the control that asked for it has handled its click.
    switch = partial(switch_language, translations, engine, view_models)
    view_models.settings.languageChanged.connect(switch, Qt.ConnectionType.QueuedConnection)
    # exit, not quit: on macOS quit goes through terminate: and comes back as a Quit event.
    view_models.desktop.quitRequested.connect(partial(app.exit, 0))
    app.user_quit = view_models.desktop.quit
    instance.activated.connect(view_models.desktop.showWindow)
    # The installer's --quit: it replaces the files, and the service keeps the tunnel.
    instance.quit_requested.connect(view_models.desktop.leave)
    instance.received.connect(receiver.deliver)
    receiver.received.connect(view_models.open_link)
    if link is not None:
        receiver.deliver(link)
    view_models.start()
    receiver.ready()
    app.aboutToQuit.connect(view_models.shutdown)
    code = app.exec()
    view_models.settings.languageChanged.disconnect(switch)
    del switch, engine  # tear down QML before the ViewModels it binds to
    store.close()
    instance.release()  # last: ask_to_quit takes the lock let go as the UI gone
    return code
