from PySide6.QtQml import QQmlApplicationEngine
from pytestqt.qtbot import QtBot

from mihomo_desktop.app import load_ui
from mihomo_desktop.viewmodels.composition import compose
from tests.support.simulated import SampleProfiles, SimulatedHost


def test_main_window_loads_without_qml_warnings(qtbot: QtBot) -> None:
    view_models = compose(SimulatedHost(), SampleProfiles())
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    try:
        assert load_ui(engine, view_models)
        view_models.start()
        qtbot.waitUntil(lambda: view_models.overview.property("connected"))
        view_models.shell.setPage("proxies")
        qtbot.waitUntil(lambda: view_models.proxies.property("currentGroup") != "")
        assert warnings == []
    finally:
        view_models.shutdown()
        for root in engine.rootObjects():
            root.deleteLater()
        del engine
