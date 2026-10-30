"""Composition root: builds models, wraps them in ViewModels and loads the QML view."""

from __future__ import annotations

import os
import sys
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from solaris import __version__
from solaris.desktop.instance import SingleInstance
from solaris.desktop.links import LinkReceiver, from_arguments
from solaris.desktop.login import BACKGROUND_ARG, LoginItem, default_login_item
from solaris.desktop.tray import tray_supported
from solaris.host.base import HostError, TunnelHost
from solaris.host.service import ServiceHost
from solaris.host.transport import Opener, Streams
from solaris.i18n import Translations, resolve_language
from solaris.models.settings import DatabaseSettingsStore, SettingsStore
from solaris.models.store import ProfileStore, default_data_dir
from solaris.models.subscription import Fetcher, UrlFetcher
from solaris.product import DISPLAY_NAME, PIPE_NAME
from solaris.update.updater import Updater
from solaris.viewmodels.appearance import Appearance, resolve_mode
from solaris.viewmodels.composition import ViewModels, compose

MAIN_QML = Path(__file__).resolve().parent / "ui" / "Main.qml"
DEV_SOCKET_ENV = "SOLARIS_DEV_SOCKET"


def default_opener() -> Opener:
    """The installed service on Windows; elsewhere the development socket, if one is set.

    The macOS extension host is not built yet (docs/architecture.md §6)."""
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


def load_ui(engine: QQmlApplicationEngine, view_models: ViewModels) -> bool:
    engine.setInitialProperties(view_models.initial_properties())
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
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
) -> int:
    """``language`` is ``zh`` or ``en`` and wins over the settings at startup; empty follows
    the settings, then SOLARIS_LANG, then the system. ``appearance`` is ``system``,
    ``light`` or ``dark`` and likewise wins over the settings; empty follows the settings,
    then SOLARIS_APPEARANCE, then the system. The settings live in the profiles'
    database, and the lock that keeps one UI per user beside it; the lock is taken before
    the database is opened, so only one UI migrates it. The store is closed on exit."""
    app = QGuiApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    # Closing the window hides it in the tray or quits explicitly (DesktopViewModel).
    app.setQuitOnLastWindowClosed(False)
    # macOS sends install links as Apple Events, which can come before the window does.
    receiver = LinkReceiver(app)
    link = from_arguments(sys.argv[1:])
    directory = store.directory if store else default_data_dir()
    instance = SingleInstance(directory)
    if not instance.claim(link):
        return 0  # the UI already running shows its window, or opens the link
    store = store or ProfileStore(directory)
    settings = DatabaseSettingsStore(store.database)
    translations = Translations(app)
    translations.apply(resolve_language(language or settings.current.language))
    view_models = compose(
        host or ServiceHost(default_opener()),
        store,
        fetcher or UrlFetcher(),
        appearance=Appearance(resolve_mode(appearance or stored_appearance(settings))),
        settings=settings,
        login_item=login_item or default_login_item(),
        tray_available=tray_supported(app.platformName()),
        start_hidden=BACKGROUND_ARG in sys.argv[1:],
        updater=updater,
    )
    engine = QQmlApplicationEngine()
    if not load_ui(engine, view_models):
        instance.release()
        store.close()
        return 1
    # Queued: the switch happens once the control that asked for it has handled its click.
    switch = partial(switch_language, translations, engine, view_models)
    view_models.settings.languageChanged.connect(switch, Qt.ConnectionType.QueuedConnection)
    view_models.desktop.quitRequested.connect(app.quit)
    instance.activated.connect(view_models.desktop.showWindow)
    instance.received.connect(receiver.deliver)
    receiver.received.connect(view_models.open_link)
    if link is not None:
        receiver.deliver(link)
    view_models.start()
    receiver.ready()
    app.aboutToQuit.connect(view_models.shutdown)
    code = app.exec()
    instance.release()
    view_models.settings.languageChanged.disconnect(switch)
    del switch, engine  # tear down QML before the ViewModels it binds to
    store.close()
    return code
