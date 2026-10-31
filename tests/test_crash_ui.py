"""The settings actions ask before exporting raw reports or clearing app logs."""

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from tests.support.window import Loaded, kind


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
def test_crash_clear_can_be_cancelled_and_preserves_normal_data(
    qtbot: QtBot, loaded: Loaded
) -> None:
    models = loaded.view_models
    store = models.library._core.store
    directory = store.directory / "crashes"
    directory.mkdir(exist_ok=True)
    report = directory / "ui-exception-1-1.log"
    report.write_text("exception")
    profiles = store.load()
    models.shell.setPage("settings")
    button = loaded.item(
        lambda item: kind(item) == "AppButton" and item.property("text") in ("清除…", "Clear…")
    )
    button.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(lambda: loaded.on_screen(button))
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: loaded.focused_text() in ("取消", "Cancel"))
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert report.exists()
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: loaded.focused_text() in ("取消", "Cancel"))
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() in ("清除", "Clear")
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not report.exists())
    assert store.load() == profiles
    assert loaded.warnings == []
