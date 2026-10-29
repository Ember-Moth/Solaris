"""Composition root: builds models, wraps them in ViewModels and loads the QML view."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mihomo_desktop import __version__
from mihomo_desktop.host.base import HostError, TunnelHost
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Opener, Streams
from mihomo_desktop.models.profiles import NoProfiles, ProfileSource
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


def main(host: TunnelHost | None = None, profiles: ProfileSource | None = None) -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    view_models = compose(host or ServiceHost(default_opener()), profiles or NoProfiles())
    engine = QQmlApplicationEngine()
    if not load_ui(engine, view_models):
        return 1
    view_models.start()
    app.aboutToQuit.connect(view_models.shutdown)
    code = app.exec()
    del engine  # tear down QML before the ViewModels it binds to
    return code
