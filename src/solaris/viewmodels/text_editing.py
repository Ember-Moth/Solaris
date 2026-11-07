"""Prepare whole-grapheme selections; Qt still owns deletion, undo and edited signals. And
end the editing in a text field when the user clicks elsewhere in the window."""

from functools import partial

from PySide6.QtCore import (
    Q_ARG,
    QEvent,
    QMetaObject,
    QObject,
    QRectF,
    Qt,
    QTextBoundaryFinder,
    QTimer,
    Slot,
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtQml import QmlElement, QmlSingleton
from PySide6.QtQuick import QQuickItem, QQuickWindow

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlSingleton
class TextEditing(QObject):
    @Slot(QObject, int, int)
    def prepareDeletion(self, editor: QObject, key: int, modifiers: int) -> None:
        """Plain Delete/Backspace only; let the IME, word shortcuts and read-only controls
        retain their own behavior. All offsets here are Qt's UTF-16 positions.
        """
        if (
            key not in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
            or modifiers != 0
            or editor.property("readOnly")
            or editor.property("inputMethodComposing")
        ):
            return
        text = editor.property("text")
        start, end = editor.property("selectionStart"), editor.property("selectionEnd")
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        finder.setPosition(start)
        if start == end:
            if not finder.isAtBoundary():
                start = finder.toPreviousBoundary()
                end = finder.toNextBoundary()
            elif key == Qt.Key.Key_Backspace:
                start = finder.toPreviousBoundary()
            else:
                end = finder.toNextBoundary()
        else:
            if not finder.isAtBoundary():
                start = finder.toPreviousBoundary()
            finder.setPosition(end)
            if not finder.isAtBoundary():
                end = finder.toNextBoundary()
        if 0 <= start < end:
            QMetaObject.invokeMethod(editor, "select", Q_ARG(int, start), Q_ARG(int, end))


class ClickAway(QObject):
    """Ends the editing in a text field or editor when the user clicks elsewhere in the
    window (DESIGN.md "输入框"), as clicking the background of a native window or a web page
    does: a press outside the one that has the focus, on something that does not take the
    focus itself, takes it from the field. It goes to the field's focus scope, so a field in
    a dialog leaves it inside the dialog. A setting's field commits as it loses the focus.

    ``owner``, such as the engine, is an object Python keeps. Nothing keeps the wrapper of the
    window the engine made, and the collector frees it and its children's wrappers with it;
    Qt would then call the filter on a new wrapper, one ``__init__`` never set up."""

    def __init__(self, window: QQuickWindow, owner: QObject) -> None:
        super().__init__(owner)
        self._window = window
        window.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self._window
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and (editor := self._window.activeFocusItem()) is not None
            and is_editor(editor)
            and not frame(editor).contains(event.scenePosition())
        ):
            # The press is delivered after this; whatever it focuses has the focus by then.
            QTimer.singleShot(0, self, partial(self._release, editor))
        return super().eventFilter(watched, event)

    def _release(self, editor: QQuickItem) -> None:
        if self._window.activeFocusItem() is editor:
            editor.setFocus(False, Qt.FocusReason.MouseFocusReason)


def is_editor(item: QQuickItem) -> bool:
    return item.inherits("QQuickTextInput") or item.inherits("QQuickTextEdit")


def frame(editor: QQuickItem) -> QRectF:
    """The box the user sees as ``editor``, in the window: a text area scrolls inside its
    scroll view (CodeEditor), and may reach far past it."""
    box = editor
    content = editor.parentItem()
    view = content.parentItem() if content is not None else None
    if editor.inherits("QQuickTextEdit") and view is not None and view.inherits("QQuickFlickable"):
        box = view.parentItem() or view
    return box.mapRectToScene(QRectF(0, 0, box.width(), box.height()))
