import os
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication

from tests.support.i18n import ui_language

# Qt renders headless in tests; set before PySide6 creates the application.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def english(qapp: QCoreApplication) -> Iterator[None]:
    """Show the UI in English; tests otherwise see the Chinese source strings."""
    with ui_language("en"):
        yield
