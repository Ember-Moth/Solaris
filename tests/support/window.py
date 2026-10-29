"""The main window under test, and ways to look into it: its items, their texts and what the
keyboard reaches."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from mihomo_desktop.viewmodels.composition import ViewModels


def find(item: QQuickItem, test: Callable[[QQuickItem], bool]) -> QQuickItem | None:
    """The first visible item under ``item`` that passes ``test``."""
    if item.isVisible() and test(item):
        return item
    for child in item.childItems():
        if (found := find(child, test)) is not None:
            return found
    return None


def find_all(item: QQuickItem, test: Callable[[QQuickItem], bool]) -> list[QQuickItem]:
    """Every visible item under ``item`` that passes ``test``."""
    if not item.isVisible():
        return []
    found = [item] if test(item) else []
    for child in item.childItems():
        found += find_all(child, test)
    return found


def texts(item: QQuickItem) -> set[str]:
    """Every ``text`` shown under ``item``."""
    found = {text} if isinstance(text := item.property("text"), str) and text else set()
    for child in item.childItems():
        found |= texts(child)
    return found


def kind(item: QQuickItem) -> str:
    """The QML type an item was declared as, such as ``AppButton``."""
    return item.metaObject().className().split("_QML")[0]


class Loaded:
    def __init__(
        self,
        view_models: ViewModels,
        engine: QQmlApplicationEngine,
        window: QQuickWindow,
        warnings: list[str],
    ) -> None:
        self.view_models = view_models
        self.engine = engine
        self.window = window
        self.warnings = warnings

    def focused(self) -> QQuickItem:
        item = self.window.activeFocusItem()
        assert item is not None
        return item

    def focused_text(self) -> str | None:
        item = self.window.activeFocusItem()
        return None if item is None else item.property("text")

    def key(
        self,
        qtbot: QtBot,
        key: Qt.Key,
        modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
        wait: int = 250,  # longer than any overlay transition
    ) -> None:
        QTest.keyClick(self.window, key, modifier)
        qtbot.wait(wait)

    def type(self, qtbot: QtBot, text: str) -> None:
        """Type ``text`` into the focused field; QTest.keyClicks takes widgets only."""
        for char in text:
            for action in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                event = QKeyEvent(action, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, char)
                QGuiApplication.sendEvent(self.window, event)
        qtbot.wait(50)

    def click(self, qtbot: QtBot, item: QQuickItem, x: float | None = None) -> None:
        middle = QPointF(item.width() / 2 if x is None else x, item.height() / 2)
        point = item.mapToScene(middle).toPoint()
        button, modifier = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
        QTest.mouseClick(self.window, button, modifier, point)
        qtbot.wait(250)

    def item(self, test: Callable[[QQuickItem], bool]) -> QQuickItem:
        found = find(self.window.contentItem(), test)
        assert found is not None
        return found

    def overlay_items(self, test: Callable[[QQuickItem], bool]) -> list[QQuickItem]:
        """Items in the window's overlays, such as an open menu or dialog."""
        root = self.window.contentItem().parentItem() or self.window.contentItem()
        return find_all(root, test)

    def focus_ring(self, item: QQuickItem) -> QQuickItem | None:
        """The focus ring drawn for ``item``, if one shows: a control draws its own, and a
        text area its scroll view's."""
        for owner in (item, item.parentItem()):
            if owner is None:
                continue
            for ring in find_all(owner, lambda i: kind(i) == "FocusRing"):
                if ring.isVisible() and ring.opacity() > 0:
                    return ring
        return None

    def on_screen(self, item: QQuickItem) -> bool:
        """Whether ``item`` shows inside the window and every view that clips it: wholly, or
        as much of it as the narrowest of them has room for."""
        area = item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))
        bounds = QRectF(0, 0, self.window.width(), self.window.height())
        parent = item.parentItem()
        while parent is not None:
            if parent.clip():
                bounds = bounds.intersected(
                    parent.mapRectToScene(QRectF(0, 0, parent.width(), parent.height()))
                )
            parent = parent.parentItem()
        shown = area.intersected(bounds)
        return (
            shown.width() >= min(area.width(), bounds.width()) - 1
            and shown.height() >= min(area.height(), bounds.height()) - 1
        )
