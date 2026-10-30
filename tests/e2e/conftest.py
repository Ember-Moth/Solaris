"""The UI against the real development service (roadmap A2).

Every test starts the service as a child process with its own socket and mihomo home, keeps
the profiles and settings in a temporary directory and drives the real ViewModels with the
QML loaded offscreen. Traffic goes to local servers through the mixed port, so nothing
leaves the machine and the machine's network does not change (docs/architecture.md §11).

The tests only run with MIHOMO_DESKTOP_E2E=1. They build the service with ``go build``
unless MIHOMO_DESKTOP_E2E_SERVICE names a binary built already.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import load_ui
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.models.settings import DatabaseSettingsStore
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.models.subscription import UrlFetcher
from mihomo_desktop.product import DISPLAY_NAME
from mihomo_desktop.viewmodels.composition import compose
from scripts.dev import DevService, build_service
from tests.e2e.app import App, Network
from tests.e2e.servers import ProxyNode, WebServer, free_port
from tests.e2e.unix import unix_opener
from tests.support.i18n import ui_language

E2E_ENV = "MIHOMO_DESKTOP_E2E"
SERVICE_ENV = "MIHOMO_DESKTOP_E2E_SERVICE"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        reason = "the development service runs on macOS and Linux"
    elif not os.environ.get(E2E_ENV):
        reason = f"set {E2E_ENV}=1 to run against the development service"
    else:
        return
    for item in items:
        if item.get_closest_marker("e2e"):
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def service_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if given := os.environ.get(SERVICE_ENV):
        return Path(given)
    return build_service(tmp_path_factory.mktemp("service") / "service")


@pytest.fixture
def service(service_binary: Path, tmp_path: Path) -> Iterator[DevService]:
    # tmp_path is too long for a Unix socket on macOS.
    with tempfile.TemporaryDirectory(prefix="md-e2e-") as run:
        socket = Path(run) / "control.sock"
        with DevService(
            service_binary, socket, tmp_path / "dev-core", tmp_path / "service.log"
        ) as dev:
            yield dev


@pytest.fixture
def network() -> Iterator[Network]:
    with (
        WebServer("proxied") as proxied,
        WebServer("direct") as direct,
        ProxyNode("node-a") as node_a,
        ProxyNode("node-b") as node_b,
    ):
        yield Network(proxied, direct, node_a, node_b)


@pytest.fixture
def app(qtbot: QtBot, tmp_path: Path, service: DevService, network: Network) -> Iterator[App]:
    """The main window on the development service, connected to nothing yet, with a free
    mixed port in the settings."""
    data = tmp_path / "data"
    store = ProfileStore(data)
    settings = DatabaseSettingsStore(store.database)
    settings.update(mixed_port=free_port())
    host = ServiceHost(unix_opener(str(service.socket)))
    name = QCoreApplication.applicationName()
    QCoreApplication.setApplicationName(DISPLAY_NAME)  # the sidebar shows it
    with ui_language("zh"):
        view_models = compose(host, store, UrlFetcher(), settings=settings)
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        try:
            assert load_ui(engine, view_models)
            (window,) = engine.rootObjects()
            assert isinstance(window, QQuickWindow)
            view_models.start()
            yield App(qtbot, view_models, window, warnings, service, network, data)
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine
            QCoreApplication.setApplicationName(name)
