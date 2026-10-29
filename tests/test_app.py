from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mihomo_desktop.app import load_ui


def test_main_window_loads() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    assert load_ui(engine)
    for root in engine.rootObjects():
        root.deleteLater()
    app.processEvents()
