"""Text fields in the window (DESIGN.md "输入框"): a click elsewhere ends the editing, and a
setting's field, the mixed port, saves on Enter or as it loses the focus and puts the saved
value back on Esc."""

from PySide6.QtCore import QMetaObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from tests.support.window import Loaded, find, kind

RANGE_ERROR = "端口应在 1024–65535 之间"  # noqa: RUF001 - the UI's copy


def port_field(qtbot: QtBot, loaded: Loaded) -> QQuickItem:
    loaded.view_models.shell.setPage("settings")
    content = loaded.window.contentItem()
    qtbot.waitUntil(lambda: find(content, lambda i: i.objectName() == "portField") is not None)
    return loaded.item(lambda i: i.objectName() == "portField")


def label(loaded: Loaded) -> QQuickItem:
    """The row's label: a click there lands on nothing that takes the focus."""
    return loaded.item(lambda i: kind(i) == "UiText" and i.property("text") == "混合端口")


def edit(qtbot: QtBot, loaded: Loaded, field: QQuickItem, text: str) -> None:
    loaded.click(qtbot, field)
    assert field.hasActiveFocus()
    QMetaObject.invokeMethod(field, "selectAll")
    loaded.type(qtbot, text)


def mixed_port(loaded: Loaded) -> int:
    return loaded.view_models.settings.property("mixedPort")


def port_error(loaded: Loaded) -> str:
    return loaded.view_models.settings.property("portError")


def test_a_click_elsewhere_ends_the_editing_and_saves(qtbot: QtBot, loaded: Loaded) -> None:
    field = port_field(qtbot, loaded)
    loaded.click(qtbot, field)
    assert field.hasActiveFocus()
    assert field.property("cursorVisible")
    assert loaded.focus_ring(field) is not None  # a field shows it whenever it has the focus
    QMetaObject.invokeMethod(field, "selectAll")
    loaded.type(qtbot, "7891")
    assert mixed_port(loaded) == 7890  # nothing is saved while the user types

    loaded.click(qtbot, label(loaded))
    assert not field.hasActiveFocus()
    assert not field.property("cursorVisible")
    assert loaded.focus_ring(field) is None
    qtbot.waitUntil(lambda: mixed_port(loaded) == 7891)
    assert field.property("text") == "7891"
    assert loaded.warnings == []


def test_a_refused_port_stays_with_its_error(qtbot: QtBot, loaded: Loaded) -> None:
    field = port_field(qtbot, loaded)
    edit(qtbot, loaded, field, "80")
    loaded.click(qtbot, label(loaded))
    assert not field.hasActiveFocus()
    assert port_error(loaded) == RANGE_ERROR
    assert field.property("text") == "80"  # the error is about what the field shows
    assert field.property("error")
    assert mixed_port(loaded) == 7890


def test_esc_puts_the_saved_port_back(qtbot: QtBot, loaded: Loaded) -> None:
    field = port_field(qtbot, loaded)
    edit(qtbot, loaded, field, "80")
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert port_error(loaded) == RANGE_ERROR
    assert field.hasActiveFocus()  # Enter saves without leaving the field

    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert field.property("text") == "7890"
    assert port_error(loaded) == ""
    assert not field.property("error")
    assert not field.hasActiveFocus()
    assert mixed_port(loaded) == 7890


def test_enter_and_tab_save_the_port(qtbot: QtBot, loaded: Loaded) -> None:
    field = port_field(qtbot, loaded)
    edit(qtbot, loaded, field, "7891")
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: mixed_port(loaded) == 7891)

    qtbot.waitUntil(lambda: field.isEnabled())  # it is disabled while the tunnel takes it
    edit(qtbot, loaded, field, "7892")
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert not field.hasActiveFocus()
    qtbot.waitUntil(lambda: mixed_port(loaded) == 7892)


def test_the_port_follows_changes_made_elsewhere(qtbot: QtBot, loaded: Loaded) -> None:
    field = port_field(qtbot, loaded)
    loaded.view_models.settings.setMixedPort("7899")
    qtbot.waitUntil(lambda: field.property("text") == "7899")


def test_its_edit_menu_keeps_the_field_editing(qtbot: QtBot, loaded: Loaded) -> None:
    """The menu takes the focus while it is open, for the field: nothing is saved, the
    field's focus ring stays, and the focus comes back when it closes."""
    field = port_field(qtbot, loaded)
    edit(qtbot, loaded, field, "7891")
    point = field.mapToScene(field.boundingRect().center()).toPoint()
    QTest.mouseClick(
        loaded.window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point
    )
    qtbot.wait(250)
    assert loaded.overlay_items(lambda i: kind(i) == "AppMenuItem" and i.property("text") == "全选")
    assert not field.hasActiveFocus()
    assert loaded.focus_ring(field) is not None
    assert mixed_port(loaded) == 7890

    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert field.hasActiveFocus()
    assert mixed_port(loaded) == 7890
    loaded.click(qtbot, label(loaded))
    qtbot.waitUntil(lambda: mixed_port(loaded) == 7891)


def test_a_click_elsewhere_keeps_a_dialogs_focus_inside(qtbot: QtBot, loaded: Loaded) -> None:
    """The field lets go of the focus to its scope: a modal dialog keeps it."""
    profiles = loaded.view_models.profiles
    loaded.view_models.shell.setPage("profiles")
    profiles.editOverride("sample-a")
    qtbot.waitUntil(lambda: loaded.overlay_items(lambda i: kind(i) == "CodeEditor") != [])
    qtbot.wait(250)
    editor = loaded.overlay_items(lambda i: kind(i) == "CodeEditor")[0]
    dialog = editor.parentItem()
    while dialog is not None and not dialog.inherits("QQuickPopupItem"):
        dialog = dialog.parentItem()
    assert dialog is not None
    assert loaded.focused().inherits("QQuickTextEdit")

    title = loaded.overlay_items(
        lambda i: kind(i) == "UiText" and i.property("text") and dialog.isAncestorOf(i)
    )[0]
    loaded.click(qtbot, title)
    focused = loaded.focused()
    assert not focused.inherits("QQuickTextEdit")
    assert focused is dialog or dialog.isAncestorOf(focused)
