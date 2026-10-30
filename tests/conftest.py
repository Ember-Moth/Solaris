import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QAccessible
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from solaris.app import load_ui
from solaris.host.base import TAKEOVER_SETTINGS
from solaris.models import credentials
from solaris.models.store import ProfileStore
from solaris.viewmodels.appearance import Appearance
from solaris.viewmodels.composition import compose
from tests.support.appearance import SystemColorScheme
from tests.support.credentials import MemoryKeyring
from tests.support.i18n import ui_language
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded

# The end-to-end tests talk to the development service over a Unix socket.
collect_ignore = ["e2e"] if sys.platform == "win32" else []

# Qt renders headless in tests; set before PySide6 creates the application.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyring:
    """The system's credential store, in memory: no test reads or writes the real one."""
    memory = MemoryKeyring()
    monkeypatch.setattr(credentials, "system_keyring", lambda: memory)
    return memory


@pytest.fixture
def english(qapp: QCoreApplication) -> Iterator[None]:
    """Show the UI in English; tests otherwise see the Chinese source strings."""
    with ui_language("en"):
        yield


@pytest.fixture
def system() -> SystemColorScheme:
    """The system's appearance, light unless a test switches it."""
    return SystemColorScheme()


@pytest.fixture
def appearance(request: pytest.FixtureRequest, system: SystemColorScheme) -> Appearance:
    """Following the system, or the mode given by indirect parametrization."""
    return Appearance(getattr(request, "param", "system"), hints=system)


@pytest.fixture
def takeover(request: pytest.FixtureRequest) -> str:
    """What connecting takes over on the host (TunnelHost.takeover): the Windows service's
    switches, or what indirect parametrization says. Request it before ``loaded``."""
    return getattr(request, "param", TAKEOVER_SETTINGS)


@pytest.fixture
def loaded(
    request: pytest.FixtureRequest,
    qtbot: QtBot,
    tmp_path: Path,
    appearance: Appearance,
    takeover: str,
) -> Iterator[Loaded]:
    """The main window in Chinese, or in the language given by indirect parametrization."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language(getattr(request, "param", "zh")):
        view_models = compose(
            SimulatedHost(takeover=takeover),
            store,
            SimulatedFetcher(),
            appearance=appearance,
            updater=offline_updater(tmp_path),
        )
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        try:
            assert load_ui(engine, view_models)
            (window,) = engine.rootObjects()
            assert isinstance(window, QQuickWindow)
            view_models.start()
            yield Loaded(view_models, engine, window, warnings)
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine


@pytest.fixture
def screen_reader(qapp: QCoreApplication) -> Iterator[None]:
    """Accessibility on, as when a screen reader runs: Qt Quick Controls fill in their roles
    and names only then. Request it before ``loaded``."""
    QAccessible.setActive(True)
    try:
        yield
    finally:
        QAccessible.setActive(False)
