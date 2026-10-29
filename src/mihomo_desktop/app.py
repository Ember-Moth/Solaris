"""Composition root: builds models, wraps them in ViewModels and loads the QML view."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mihomo_desktop import __version__
from mihomo_desktop.desktop.instance import SingleInstance
from mihomo_desktop.desktop.login import BACKGROUND_ARG, LoginItem, default_login_item
from mihomo_desktop.desktop.tray import tray_supported
from mihomo_desktop.host.base import HostError, TunnelHost
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Opener, Streams
from mihomo_desktop.i18n import Translations, resolve_language
from mihomo_desktop.models.settings import SettingsStore
from mihomo_desktop.models.store import ProfileStore, default_data_dir
from mihomo_desktop.models.subscription import Fetcher, UrlFetcher
from mihomo_desktop.product import DISPLAY_NAME, PIPE_NAME
from mihomo_desktop.viewmodels.composition import ViewModels, compose

MAIN_QML = Path(__file__).resolve().parent / "ui" / "Main.qml"
DEV_SOCKET_ENV = "MIHOMO_DESKTOP_DEV_SOCKET"


def default_opener() -> Opener:
    """The installed service on Windows; elsewhere the development socket, if one is set.

    The macOS extension host is not built yet (docs/architecture.md §6)."""
    if sys.platform == "win32":
        from mihomo_desktop.host.transport import pipe_opener

        return pipe_opener(PIPE_NAME)
    from mihomo_desktop.host.transport import unix_opener

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


def main(
    host: TunnelHost | None = None,
    store: ProfileStore | None = None,
    fetcher: Fetcher | None = None,
    language: str = "",
    login_item: LoginItem | None = None,
) -> int:
    """``language`` is ``zh`` or ``en`` and wins over the settings at startup; empty follows
    the settings, then MIHOMO_DESKTOP_LANG, then the system. The settings and the lock that
    keeps one UI per user live beside the profiles."""
    app = QGuiApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    # Closing the window hides it in the tray or quits explicitly (DesktopViewModel).
    app.setQuitOnLastWindowClosed(False)
    store = store or ProfileStore(default_data_dir())
    instance = SingleInstance(store.directory)
    if not instance.claim():
        return 0  # the UI already running shows its window
    settings = SettingsStore(store.directory)
    translations = Translations(app)
    translations.apply(resolve_language(language or settings.current.language))
    view_models = compose(
        host or ServiceHost(default_opener()),
        store,
        fetcher or UrlFetcher(),
        settings=settings,
        login_item=login_item or default_login_item(),
        tray_available=tray_supported(app.platformName()),
        start_hidden=BACKGROUND_ARG in sys.argv[1:],
    )
    engine = QQmlApplicationEngine()
    if not load_ui(engine, view_models):
        instance.release()
        return 1
    view_models.desktop.quitRequested.connect(app.quit)
    instance.activated.connect(view_models.desktop.showWindow)
    view_models.start()
    app.aboutToQuit.connect(view_models.shutdown)
    code = app.exec()
    instance.release()
    del engine  # tear down QML before the ViewModels it binds to
    return code
