"""Context menus (DESIGN.md "右键菜单"): text fields and editors get the app's edit menu in
place of Qt's, and a right-click on a row opens the menu its more button opens."""

import re
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from PySide6.QtCore import Q_ARG, QEvent, QMetaObject, QObject, QPoint, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from solaris.app import MAIN_QML
from tests.support.i18n import ui_language
from tests.support.window import Loaded, find, find_all, kind

UI = MAIN_QML.parent
EDITORS = b"""import QtQuick
import QtQuick.Controls.Basic
import "components"
ApplicationWindow {
    visible: true
    width: 640; height: 480
    AppTextField { objectName: "field"; width: 300; x: 20; y: 20 }
    AppTextField { objectName: "secret"; width: 300; x: 20; y: 70; echoMode: TextInput.Password }
    CodeEditor {
        objectName: "viewer"; width: 300; height: 160; x: 20; y: 120
        readOnly: true; text: "mixed-port: 7890"
    }
}"""


@dataclass
class Editors:
    window: QQuickWindow
    field: QQuickItem
    secret: QQuickItem
    viewer: QQuickItem

    def area(self) -> QQuickItem:
        """The viewer's text area, which takes the focus and the selection."""
        found = find(self.viewer, lambda i: i.inherits("QQuickTextEdit"))
        assert found is not None
        return found


@pytest.fixture
def clipboard(qtbot: QtBot) -> Iterator[None]:
    """Something to paste; the clipboard's own text comes back afterwards."""
    board = QGuiApplication.clipboard()
    original = board.text()
    board.setText("7891")
    try:
        yield
    finally:
        board.setText(original)


def editors_in(qtbot: QtBot, language: str) -> Iterator[Editors]:
    with ui_language(language):
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(EDITORS, QUrl.fromLocalFile(str(MAIN_QML)))
        window = component.create()
        assert isinstance(window, QQuickWindow), [e.toString() for e in component.errors()]
        items = {name: window.findChild(QQuickItem, name) for name in ("field", "secret", "viewer")}
        assert all(items.values())
        try:
            window.requestActivate()
            qtbot.waitUntil(window.isActive)
            yield Editors(window, **items)  # pyright: ignore[reportArgumentType]
        finally:
            window.close()
            window.deleteLater()


@pytest.fixture
def editors(qtbot: QtBot) -> Iterator[Editors]:
    yield from editors_in(qtbot, "zh")


@pytest.fixture
def english_editors(qtbot: QtBot) -> Iterator[Editors]:
    yield from editors_in(qtbot, "en")


def overlay(window: QQuickWindow) -> QQuickItem:
    """The window's root, which holds the overlay that menus show in."""
    root = window.contentItem()
    while (parent := root.parentItem()) is not None:
        root = parent
    return root


def right_click(qtbot: QtBot, window: QQuickWindow, item: QQuickItem, at: QPointF) -> QPoint:
    point = item.mapToScene(at).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
    qtbot.wait(250)  # longer than the menu's fade-in
    return point


def shown_items(window: QQuickWindow) -> list[tuple[str, bool]]:
    """The open menu's items, with whether each is enabled."""
    items = find_all(overlay(window), lambda i: kind(i) == "AppMenuItem")
    return [(i.property("text"), i.isEnabled()) for i in items if i.height() > 0]


def menu_box(window: QQuickWindow) -> QRectF:
    """Where the open menu shows, in the window."""
    (box,) = [
        item
        for item in find_all(overlay(window), lambda i: i.inherits("QQuickPopupItem"))
        if find(item, lambda i: kind(i) == "AppMenuItem") is not None
    ]
    return box.mapRectToScene(QRectF(0, 0, box.width(), box.height()))


def select(item: QQuickItem, start: int, end: int) -> None:
    QMetaObject.invokeMethod(item, "select", Q_ARG(int, start), Q_ARG(int, end))


def type_text(window: QQuickWindow, text: str) -> None:
    for char in text:
        for action in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = QKeyEvent(action, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, char)
            QGuiApplication.sendEvent(window, event)


def trigger(window: QQuickWindow, text: str) -> None:
    (item,) = [
        i
        for i in find_all(overlay(window), lambda i: kind(i) == "AppMenuItem")
        if i.property("text") == text
    ]
    middle = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, middle)


@pytest.mark.usefixtures("clipboard")
def test_a_fields_menu_offers_what_its_state_allows(qtbot: QtBot, editors: Editors) -> None:
    window, field = editors.window, editors.field
    field.forceActiveFocus()
    type_text(window, "7890")  # typed, so that there is something to undo
    select(field, 0, 2)
    point = right_click(qtbot, window, field, QPointF(40, 10))
    assert shown_items(window) == [
        ("撤销", True),
        ("重做", False),
        ("剪切", True),
        ("复制", True),
        ("粘贴", True),
        ("全选", True),
    ]
    # The menu's corner is at the pointer, and it sizes as DESIGN.md's menu: 4px around
    # 32px items, two separators of 1px with 4px above and below.
    box = menu_box(window)
    assert (box.x(), box.y()) == (point.x(), point.y())
    assert box.height() == 2 * 4 + 6 * 32 + 2 * 9
    assert box.width() >= 180
    # The field keeps its selection while the menu has the focus.
    assert field.property("selectedText") == "78"

    trigger(window, "剪切")
    qtbot.waitUntil(lambda: field.hasActiveFocus())
    assert field.property("text") == "90"
    assert QGuiApplication.clipboard().text() == "78"


def test_an_empty_field_has_little_to_offer(qtbot: QtBot, editors: Editors) -> None:
    QGuiApplication.clipboard().clear()
    right_click(qtbot, editors.window, editors.field, QPointF(40, 10))
    assert editors.field.hasActiveFocus() is False  # the menu holds it for the field
    assert all(not enabled for _, enabled in shown_items(editors.window))


def test_the_menu_takes_the_field_and_gives_it_back(qtbot: QtBot, editors: Editors) -> None:
    """A right-click focuses the field first, as a native one does; Esc closes the menu and
    the field has the focus again."""
    window, field = editors.window, editors.field
    field.setProperty("text", "7890")
    right_click(qtbot, window, field, QPointF(40, 10))
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(250)
    assert shown_items(window) == []
    assert field.hasActiveFocus()


def test_a_read_only_editor_copies_only(qtbot: QtBot, editors: Editors) -> None:
    window, area = editors.window, editors.area()
    right_click(qtbot, window, editors.viewer, QPointF(60, 20))
    assert shown_items(window) == [("复制", False), ("全选", True)]
    trigger(window, "全选")
    qtbot.waitUntil(lambda: area.property("selectedText") == "mixed-port: 7890")
    qtbot.wait(250)  # the menu fades out

    right_click(qtbot, window, editors.viewer, QPointF(60, 20))
    assert shown_items(window) == [("复制", True), ("全选", True)]
    assert menu_box(window).height() == 2 * 4 + 2 * 32  # no separators


@pytest.mark.usefixtures("clipboard")
def test_a_password_field_copies_nothing(qtbot: QtBot, editors: Editors) -> None:
    window, secret = editors.window, editors.secret
    secret.setProperty("text", "hunter2")
    secret.forceActiveFocus()
    QMetaObject.invokeMethod(secret, "selectAll")
    right_click(qtbot, window, secret, QPointF(40, 10))
    assert dict(shown_items(window)) == {
        "撤销": False,
        "重做": False,
        "剪切": False,
        "复制": False,
        "粘贴": True,
        "全选": True,
    }


def test_the_menu_stays_inside_the_window(qtbot: QtBot, editors: Editors) -> None:
    """Near the bottom right it opens to the left of and above the pointer."""
    window, viewer = editors.window, editors.viewer
    viewer.setProperty("x", window.width() - viewer.width() - 4)
    viewer.setProperty("y", window.height() - viewer.height() - 4)
    point = right_click(qtbot, window, viewer, QPointF(viewer.width() - 10, viewer.height() - 10))
    box = menu_box(window)
    assert (box.right(), box.bottom()) == (point.x(), point.y())
    assert box.left() >= 8
    assert box.top() >= 8


def test_the_menu_speaks_english(qtbot: QtBot, english_editors: Editors) -> None:
    right_click(qtbot, english_editors.window, english_editors.field, QPointF(40, 10))
    labels = [text for text, _ in shown_items(english_editors.window)]
    assert labels == ["Undo", "Redo", "Cut", "Copy", "Paste", "Select all"]


def test_no_text_field_falls_back_to_qts_menu() -> None:
    """The Basic style gives every TextField and TextArea a menu of its own design: only the
    two components that replace it declare one."""
    declaring = {
        path.relative_to(UI).as_posix()
        for path in UI.rglob("*.qml")
        if re.search(r"^\s*(TextField|TextArea)\s*\{", path.read_text(encoding="utf-8"), re.M)
    }
    assert declaring == {"components/AppTextField.qml", "components/CodeEditor.qml"}
    for name in declaring:
        source = (UI / name).read_text(encoding="utf-8")
        assert "ContextMenu.menu: null" in source
        assert "EditContextMenu {" in source


def open_menu(window: QQuickWindow) -> QObject:
    (menu,) = [
        obj
        for obj in window.findChildren(QObject)
        if kind(obj) == "AppMenu" and obj.property("visible")  # pyright: ignore[reportArgumentType]
    ]
    return menu


def test_a_rows_right_click_opens_its_more_menu(qtbot: QtBot, loaded: Loaded) -> None:
    window = loaded.window
    loaded.view_models.shell.setPage("profiles")
    qtbot.waitUntil(
        lambda: find(window.contentItem(), lambda i: kind(i) == "ProfileRow") is not None
    )
    qtbot.wait(250)  # the page shows
    row = loaded.item(lambda i: kind(i) == "ProfileRow")
    more = find(row, lambda i: kind(i) == "IconButton")
    assert more is not None

    point = right_click(qtbot, window, row, QPointF(180, 20))
    menu = open_menu(window)
    assert menu.property("trigger") == more  # dialogs its items open hand focus back there
    texts = [text for text, _ in shown_items(window)]
    assert texts[-1] == "删除"
    assert "编辑覆写…" in texts
    box = menu_box(window)
    assert (box.x(), box.y()) == (point.x(), point.y())
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(250)

    # The more button opens the same menu, under itself.
    loaded.click(qtbot, more)
    assert open_menu(window) == menu
    assert shown_items(window) == [(text, True) for text in texts]
    below = more.mapRectToScene(QRectF(0, 0, more.width(), more.height()))
    assert menu_box(window).right() == below.right()
    assert loaded.warnings == []


def test_a_group_rows_right_click_opens_its_more_menu(qtbot: QtBot, loaded: Loaded) -> None:
    """Built-in and region groups open the menu of types, custom groups their own: whichever
    the row's more button opens (docs/routing.md §4.5)."""
    window, view_models = loaded.window, loaded.view_models
    routing = view_models.routing
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage("routing")
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: routing.property("summary").endswith("27 个节点"))
    editor = routing._group_editor
    with qtbot.waitSignal(editor.opened):
        editor.openAdd()
    editor.setName("香港精选")
    editor.toggleRegion("HK")
    with qtbot.waitSignal(editor.saved):
        editor.save()
    page = loaded.item(lambda i: kind(i) == "RoutingPage")
    page.setProperty("tab", "groups")
    qtbot.wait(250)  # the dialog closes

    def row(custom: bool) -> tuple[QQuickItem, QQuickItem]:
        found = find(
            page,
            lambda i: (
                kind(i) == "GroupRow"
                and bool(i.property("groupId"))
                and i.property("custom") is custom
            ),
        )
        assert found is not None
        more = find(found, lambda i: kind(i) == "IconButton")
        assert more is not None
        view = found.parentItem()
        while not view.inherits("QQuickFlickable"):
            view = view.parentItem()
        top = found.mapToItem(view.property("contentItem"), QPointF(0, 0)).y()
        view.setProperty("contentY", max(0.0, top - 40))
        qtbot.wait(50)
        assert loaded.on_screen(found)
        return found, more

    built_in, more = row(custom=False)
    point = right_click(qtbot, window, built_in, QPointF(60, built_in.height() / 2))
    assert open_menu(window).property("trigger") == more
    texts = [text for text, _ in shown_items(window)]
    assert texts[-2:] == ["隐藏", "复制为自建策略组…"]
    box = menu_box(window)
    assert (box.x(), box.y()) == (point.x(), point.y())
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(250)

    custom, more = row(custom=True)
    point = right_click(qtbot, window, custom, QPointF(60, custom.height() / 2))
    assert open_menu(window).property("trigger") == more
    assert [text for text, _ in shown_items(window)] == ["编辑…", "复制…", "删除"]
    box = menu_box(window)
    assert (box.x(), box.y()) == (point.x(), point.y())
    with qtbot.waitSignal(editor.opened):
        trigger(window, "编辑…")
    assert editor.property("title") == "编辑策略组「香港精选」"
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(250)
    assert loaded.warnings == []
