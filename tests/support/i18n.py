"""Switch the running application's UI language for one test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QCoreApplication, QLocale

from mihomo_desktop.i18n import SOURCE_LANGUAGE, Translations


@contextmanager
def ui_language(language: str) -> Iterator[None]:
    app = QCoreApplication.instance()
    assert app is not None, "needs the qapp fixture"
    locale = QLocale()
    translations = Translations(app)
    translations.apply(language)
    try:
        yield
    finally:
        translations.apply(SOURCE_LANGUAGE)
        QLocale.setDefault(locale)
