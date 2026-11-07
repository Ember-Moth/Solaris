"""The UI language: which one to show, and the translator that shows it.

Source strings are Simplified Chinese; ``translations/<language>.qm`` carries the others.
scripts/gen_i18n.py extracts the strings and compiles the catalogs (docs/ui.md §5).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"
SOURCE_LANGUAGE = "zh"
LANGUAGES = ("zh", "en")
LANG_ENV = "SOLARIS_LANG"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CF_UTF8 = 0x08000100  # kCFStringEncodingUTF8


def QT_TRANSLATE_NOOP(context: str, text: str) -> str:  # noqa: N802 - the name lupdate looks for
    """Mark ``text`` for lupdate without translating it; translate it where it is shown.

    PySide6's own QT_TRANSLATE_NOOP is typed as returning ``object``."""
    return text


def current_language() -> str:
    """The language the installed translator shows, for text the catalog does not hold, such
    as the service list's names (models/services.py): ``zh`` without a translator."""
    shown = QCoreApplication.translate("Language", "zh")
    return shown if shown in LANGUAGES else SOURCE_LANGUAGE


def system_ui_languages() -> list[str]:
    """The system's UI languages, most preferred first, such as ``zh-Hans-CN``.

    On macOS PySide6's ``QLocale.system()`` is not the system's: it is built from Python's
    ``locale.getlocale()``, so from LANG and LC_*, which an app opened from Finder does not
    have, and is C there. The languages come from CoreFoundation instead, as they do in Qt's
    own backend (qlocale_mac.mm)."""
    if sys.platform == "darwin":
        return _mac_ui_languages()
    return QLocale.system().uiLanguages()


def system_locale() -> QLocale:
    """The system's locale; on macOS CoreFoundation's, as for ``system_ui_languages``."""
    if sys.platform == "darwin":
        return QLocale(_mac_locale_name())
    return QLocale.system()


def system_language(ui_languages: Sequence[str] | None = None) -> str:
    """Chinese when the system's first UI language is any Chinese, English otherwise."""
    languages = system_ui_languages() if ui_languages is None else ui_languages
    return "zh" if languages and languages[0].lower().startswith("zh") else "en"


def resolve_language(
    preference: str = "",
    environ: Mapping[str, str] = os.environ,
    ui_languages: Sequence[str] | None = None,
) -> str:
    """The language to show: ``preference`` (the settings page), then
    ``SOLARIS_LANG``, then the system's. Anything but ``zh`` or ``en`` falls through."""
    for choice in (preference, environ.get(LANG_ENV, "")):
        if choice.strip().lower() in LANGUAGES:
            return choice.strip().lower()
    return system_language(ui_languages)


def ui_locale(language: str, system: QLocale | None = None) -> QLocale:
    """The locale that formats dates: the system's when it speaks ``language``, so an en_GB
    system keeps its conventions, otherwise the language's default (zh_CN, en_US)."""
    system = system_locale() if system is None else system
    default = QLocale(language)
    return system if system.language() == default.language() else default


@cache
def _core_foundation() -> ctypes.CDLL:
    library = ctypes.CDLL(_CORE_FOUNDATION)
    ref = ctypes.c_void_p
    for name, restype, argtypes in (
        ("CFLocaleCopyPreferredLanguages", ref, []),
        ("CFLocaleCopyCurrent", ref, []),
        ("CFLocaleGetIdentifier", ref, [ref]),
        ("CFArrayGetCount", ctypes.c_long, [ref]),
        ("CFArrayGetValueAtIndex", ref, [ref, ctypes.c_long]),
        (
            "CFStringGetCString",
            ctypes.c_bool,
            [ref, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32],
        ),
        ("CFRelease", None, [ref]),
    ):
        function = getattr(library, name)
        function.restype, function.argtypes = restype, argtypes
    return library


def _cf_string(string: int | None) -> str:
    buffer = ctypes.create_string_buffer(256)
    if not string or not _core_foundation().CFStringGetCString(string, buffer, 256, _CF_UTF8):
        return ""
    return buffer.value.decode()


def _mac_ui_languages() -> list[str]:
    cf = _core_foundation()
    languages = cf.CFLocaleCopyPreferredLanguages()
    if not languages:
        return []
    try:
        count = cf.CFArrayGetCount(languages)
        tags = (_cf_string(cf.CFArrayGetValueAtIndex(languages, i)) for i in range(count))
        return [tag for tag in tags if tag]
    finally:
        cf.CFRelease(languages)


def _mac_locale_name() -> str:
    cf = _core_foundation()
    locale = cf.CFLocaleCopyCurrent()
    if not locale:
        return ""
    try:
        return _cf_string(cf.CFLocaleGetIdentifier(locale))
    finally:
        cf.CFRelease(locale)


class Translations:
    """Installs one language's translator and default locale on the application: the UI's
    catalog, which Chinese, the source language, does without.

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
