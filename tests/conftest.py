import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QAccessible
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import load_ui
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.appearance import Appearance
from mihomo_desktop.viewmodels.composition import compose
from tests.support.appearance import SystemColorScheme
from tests.support.i18n import ui_language
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded

# Qt renders headless in tests; set before PySide6 creates the application.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
def loaded(
    request: pytest.FixtureRequest, qtbot: QtBot, tmp_path: Path, appearance: Appearance
) -> Iterator[Loaded]:
    """The main window in Chinese, or in the language given by indirect parametrization."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language(getattr(request, "param", "zh")):
        view_models = compose(SimulatedHost(), store, SimulatedFetcher(), appearance=appearance)
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
