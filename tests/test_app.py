import time
from pathlib import Path

from PySide6.QtQml import QQmlApplicationEngine
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import load_ui
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.composition import compose
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles


def test_main_window_loads_without_qml_warnings(qtbot: QtBot, tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    view_models = compose(SimulatedHost(), store, SimulatedFetcher())
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    try:
        assert load_ui(engine, view_models)
        view_models.start()
        qtbot.waitUntil(lambda: view_models.overview.property("connected"))
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
