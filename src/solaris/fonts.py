"""Application fonts and text/emoji fallbacks shared by both Qt platforms.

Register before creating QML objects: FontLoader alone does not override the platform's
fallback choices. The entire ui/fonts directory ships in both standalone packages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

DIRECTORY = Path(__file__).resolve().parent / "ui" / "fonts"
BUNDLED = (
    ("inter/Inter-Regular.ttf", "Inter"),
    ("inter/Inter-Medium.ttf", "Inter"),
    ("inter/Inter-SemiBold.ttf", "Inter"),
    ("jetbrains-mono/JetBrainsMono-Regular.ttf", "JetBrains Mono"),
    ("noto-sans-sc/NotoSansSC-VF.ttf", "Noto Sans SC"),
    ("noto-sans-symbols-2/NotoSansSymbols2-Regular.ttf", "Noto Sans Symbols 2"),
    # Keep the upstream hybrid font: macOS 26 needs its SVG color glyphs while
    # Windows and newer CoreText can use COLRv1. A COLRv1-only font can load and
    # shape correctly on older CoreText yet paint every emoji in monochrome.
    ("noto-color-emoji/NotoColorEmoji-Regular.ttf", "Noto Color Emoji"),
)
_REGISTERED = "_solaris_fonts_registered"
_log = logging.getLogger(__name__)


def register_fonts() -> None:
    """Once per GUI application, including previews and tests with multiple QML engines.

    Missing assets are diagnosed and leave system fallback available; an installed font with
    the same name must not disguise a missing bundled asset in diagnostics.
    """
    app = QGuiApplication.instance()
    if not isinstance(app, QGuiApplication):
        raise RuntimeError("register_fonts requires a QGuiApplication")
    if app.property(_REGISTERED):
        return

    loaded: set[str] = set()
    for relative, family in BUNDLED:
        path = DIRECTORY / relative
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0 or family not in QFontDatabase.applicationFontFamilies(font_id):
            _log.error("Cannot load bundled font %s from %s", family, path)
            if font_id >= 0:
                QFontDatabase.removeApplicationFont(font_id)
            continue
        loaded.add(family)

    fallback = [name for name in ("Noto Sans SC", "Noto Sans Symbols 2") if name in loaded]
    # PySide does not expose QChar or QFontDatabase's script fallback methods. Family
    # substitutions supply this ordered fallback for every primary face the UI uses,
    # including the system face used if a Latin asset could not be registered.
    for family in ("Inter", "JetBrains Mono", app.font().family()):
        QFont.removeSubstitutions(family)
        QFont.insertSubstitutions(family, [name for name in fallback if name != family])
    QFontDatabase.setApplicationEmojiFontFamilies(
        ["Noto Color Emoji"] if "Noto Color Emoji" in loaded else []
    )
    if "Inter" in loaded:
        font = app.font()
        font.setFamily("Inter")
        app.setFont(font)
    app.setProperty(_REGISTERED, True)
