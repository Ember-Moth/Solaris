"""Appearance: light or dark, following the system or fixed by the user (DESIGN.md "深色").

Theme.qml binds its colors to ``Appearance.dark``, so a change repaints the window at once.
The settings page sets ``mode`` and stores it (docs/ui.md §6).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, ClassVar, Protocol

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

MODES = ("system", "light", "dark")
APPEARANCE_ENV = "SOLARIS_APPEARANCE"


def resolve_mode(preference: str = "", environ: Mapping[str, str] = os.environ) -> str:
    """The mode to start in: ``preference`` (the settings page), then
    ``SOLARIS_APPEARANCE``, then ``system``. Anything not in MODES falls through."""
    for choice in (preference, environ.get(APPEARANCE_ENV, "")):
        if choice.strip().lower() in MODES:
            return choice.strip().lower()
    return "system"


class ColorSchemeHints(Protocol):
    """The part of QStyleHints that Appearance uses; tests pass a stand-in."""

    colorSchemeChanged: ClassVar[Any]  # Signal(Qt.ColorScheme)

    def colorScheme(self) -> Qt.ColorScheme: ...
    def setColorScheme(self, scheme: Qt.ColorScheme, /) -> None: ...
    def unsetColorScheme(self) -> None: ...


@QmlElement
@QmlUncreatable("Appearance is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class Appearance(QObject):
    """``mode`` is the user's choice, ``dark`` the result the UI shows.

    In ``system`` mode ``dark`` follows the system's color scheme, also while the app runs.
    A fixed mode asks Qt to use that scheme for the app as well, so the native title bar
    matches; platforms without that support (such as the offscreen one) keep their frame."""

    modeChanged = Signal()
    darkChanged = Signal()

    def __init__(
        self,
        mode: str = "system",
        hints: ColorSchemeHints | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        _check(mode)
        self._hints: ColorSchemeHints = hints if hints is not None else QGuiApplication.styleHints()
        self._mode = mode
        self._request_scheme()
        self._dark = self._resolve()
        self._hints.colorSchemeChanged.connect(self._update)

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        """``system``, ``light`` or ``dark``."""
        return self._mode

    @Property(bool, notify=darkChanged)
    def dark(self) -> bool:
        return self._dark

    @Slot(str)
    def setMode(self, mode: str) -> None:
        _check(mode)
        if mode == self._mode:
            return
        self._mode = mode
        self._request_scheme()
        self.modeChanged.emit()
        self._update()

    def _request_scheme(self) -> None:
        if self._mode == "system":
            self._hints.unsetColorScheme()
        else:
            dark = self._mode == "dark"
            self._hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

    def _resolve(self) -> bool:
        if self._mode == "system":
            return self._hints.colorScheme() == Qt.ColorScheme.Dark
        return self._mode == "dark"

    def _update(self) -> None:
        dark = self._resolve()
        if dark != self._dark:
            self._dark = dark
            self.darkChanged.emit()


def _check(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unsupported appearance: {mode!r}")
