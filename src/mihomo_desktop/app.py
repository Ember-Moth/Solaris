"""Composition root: builds models, wraps them in ViewModels and loads the QML view.

Only the QML shell exists so far; hosts are wired in once the Windows service and the
macOS extension can run a core.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mihomo_desktop import __version__
from mihomo_desktop.product import DISPLAY_NAME

MAIN_QML = Path(__file__).resolve().parent / "ui" / "Main.qml"


def load_ui(engine: QQmlApplicationEngine) -> bool:
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    return bool(engine.rootObjects())


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    engine = QQmlApplicationEngine()
    if not load_ui(engine):
        return 1
    return app.exec()
