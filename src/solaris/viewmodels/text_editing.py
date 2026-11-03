"""Prepare whole-grapheme selections; Qt still owns deletion, undo and edited signals."""

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QTextBoundaryFinder, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

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
