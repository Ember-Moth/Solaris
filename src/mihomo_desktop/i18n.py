"""The UI language: which one to show, and the translator that shows it.

Source strings are Simplified Chinese; ``translations/<language>.qm`` carries the others.
scripts/gen_i18n.py extracts the strings and compiles the catalogs (docs/ui.md §5).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
SOURCE_LANGUAGE = "zh"
LANGUAGES = ("zh", "en")
LANG_ENV = "MIHOMO_DESKTOP_LANG"


def QT_TRANSLATE_NOOP(context: str, text: str) -> str:  # noqa: N802 - the name lupdate looks for
    """Mark ``text`` for lupdate without translating it; translate it where it is shown.

    PySide6's own QT_TRANSLATE_NOOP is typed as returning ``object``."""
    return text


def current_language() -> str:
    """The language the installed translator shows, for text the catalog does not hold, such
    as the service list's names (models/services.py): ``zh`` without a translator."""
    shown = QCoreApplication.translate("Language", "zh")
    return shown if shown in LANGUAGES else SOURCE_LANGUAGE


def system_language(ui_languages: Sequence[str] | None = None) -> str:
    """Chinese when the system's first UI language is any Chinese, English otherwise."""
    languages = QLocale.system().uiLanguages() if ui_languages is None else ui_languages
    return "zh" if languages and languages[0].lower().startswith("zh") else "en"


def resolve_language(
    preference: str = "",
    environ: Mapping[str, str] = os.environ,
    ui_languages: Sequence[str] | None = None,
) -> str:
    """The language to show: ``preference`` (the settings page, roadmap M3), then
    ``MIHOMO_DESKTOP_LANG``, then the system's. Anything but ``zh`` or ``en`` falls through."""
    for choice in (preference, environ.get(LANG_ENV, "")):
        if choice.strip().lower() in LANGUAGES:
            return choice.strip().lower()
    return system_language(ui_languages)


def ui_locale(language: str, system: QLocale | None = None) -> QLocale:
    """The locale that formats dates: the system's when it speaks ``language``, so an en_GB
    system keeps its conventions, otherwise the language's default (zh_CN, en_US)."""
    system = QLocale.system() if system is None else system
    default = QLocale(language)
    return system if system.language() == default.language() else default


# Catalogs for text of Qt's own that its catalogs do not translate yet, such as the text
# fields' context menu (QtQuick.Controls.impl's CutAction and the like), by UI language.
QT_CATALOGS = {"zh": "qtquick_zh_CN"}


class Translations:
    """Installs one language's translators and default locale on the application: the UI's
    catalog, and ``QT_CATALOGS`` for Qt's own text.

    ``apply`` can run again to switch languages. A switch while the UI is up also needs
    ``QQmlEngine.retranslate()`` and the ViewModels to re-emit their text properties."""

    def __init__(self, app: QCoreApplication) -> None:
        self._app = app
        self._translators: list[QTranslator] = []
        self.language = SOURCE_LANGUAGE

    def apply(self, language: str) -> None:
        if language not in LANGUAGES:
            raise ValueError(f"unsupported language: {language!r}")
        self.remove()
        names = [] if language == SOURCE_LANGUAGE else [language]
        if language in QT_CATALOGS:
            names.append(QT_CATALOGS[language])
        for name in names:
            translator = QTranslator(self._app)
            catalog = TRANSLATIONS_DIR / f"{name}.qm"
            if not translator.load(str(catalog)):
                raise FileNotFoundError(f"cannot load {catalog}; see docs/ui.md §5")
            self._app.installTranslator(translator)
            self._translators.append(translator)
        QLocale.setDefault(ui_locale(language))
        self.language = language

    def remove(self) -> None:
        """Uninstall the translators this object installed."""
        for translator in self._translators:
            self._app.removeTranslator(translator)
        self._translators = []
