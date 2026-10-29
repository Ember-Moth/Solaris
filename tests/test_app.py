import time
from pathlib import Path

import pytest
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import load_ui
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.composition import compose
from tests.support.i18n import ui_language
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles


def texts(item: QQuickItem) -> set[str]:
    """Every ``text`` shown under ``item``."""
    found = {text} if isinstance(text := item.property("text"), str) and text else set()
    for child in item.childItems():
        found |= texts(child)
    return found


@pytest.mark.parametrize(("language", "label"), [("zh", "出站"), ("en", "Outbound")])
def test_main_window_loads_without_qml_warnings(
    qtbot: QtBot, tmp_path: Path, language: str, label: str
) -> None:
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    with ui_language(language):
        view_models = compose(SimulatedHost(), store, SimulatedFetcher())
        engine = QQmlApplicationEngine()
        warnings: list[str] = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        try:
            assert load_ui(engine, view_models)
            view_models.start()
            qtbot.waitUntil(lambda: view_models.overview.property("connected"))
            window = engine.rootObjects()[0]
            assert isinstance(window, QQuickWindow)
            assert label in texts(window.contentItem())
            view_models.shell.setPage("proxies")
            qtbot.waitUntil(lambda: view_models.proxies.property("currentGroup") != "")
            view_models.shell.setPage("profiles")
            view_models.profiles.use("sample-b")
            qtbot.waitUntil(lambda: view_models.profiles.property("activeName") == "示例订阅 B")
            assert warnings == []
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine
