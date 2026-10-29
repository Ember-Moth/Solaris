"""A stand-in for QStyleHints' color scheme, so tests can switch the system's appearance."""

from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import QObject, Qt, Signal


class SystemColorScheme(QObject):
    """The system's scheme, and the one the app asked for, which wins as it does in Qt."""

    colorSchemeChanged: ClassVar[Signal] = Signal(Qt.ColorScheme)  # noqa: N815 - QStyleHints' API

    def __init__(self, scheme: Qt.ColorScheme = Qt.ColorScheme.Light) -> None:
        super().__init__()
        self.system = scheme
        self.requested: Qt.ColorScheme | None = None

    def colorScheme(self) -> Qt.ColorScheme:  # noqa: N802
        return self.system if self.requested is None else self.requested

    def setColorScheme(self, scheme: Qt.ColorScheme, /) -> None:  # noqa: N802
        before = self.colorScheme()
        self.requested = scheme
        self._notify(before)

    def unsetColorScheme(self) -> None:  # noqa: N802
        before = self.colorScheme()
        self.requested = None
        self._notify(before)

    def switch(self, scheme: Qt.ColorScheme) -> None:
        """The user changes the system's appearance."""
        before = self.colorScheme()
        self.system = scheme
        self._notify(before)

    def _notify(self, before: Qt.ColorScheme) -> None:
        if self.colorScheme() != before:
            self.colorSchemeChanged.emit(self.colorScheme())
