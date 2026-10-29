"""The tray icon's image (docs/ui.md §8).

macOS draws a template image in the menu bar's own colours. Windows shows the image as it
is, so the notification area gets a dark glyph on a light taskbar and a white one on a
dark taskbar; the taskbar follows the system theme, not the apps' theme that Qt reports.
A disconnected tunnel dims the glyph.
"""

from __future__ import annotations

import sys
from pathlib import Path

TRAY_DIR = Path(__file__).resolve().parent.parent / "ui" / "tray"
# Platforms whose Qt plugin has a native tray. Elsewhere Qt.labs.platform would need Qt
# Widgets, which the UI does not load.
TRAY_PLATFORMS = ("cocoa", "windows")
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def tray_supported(platform_name: str) -> bool:
    """``platform_name`` is QGuiApplication.platformName()."""
    return platform_name in TRAY_PLATFORMS


def taskbar_is_light() -> bool:
    """Windows: whether the taskbar uses the light theme ("Choose your default Windows
    mode"). Windows 10 has no such value until the user sets it, and its taskbar is dark."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
    except OSError:
        return False
    return bool(value)


def tray_icon(*, template: bool, light_taskbar: bool, connected: bool) -> Path:
    variant = "template" if template else "light-taskbar" if light_taskbar else "dark-taskbar"
    return TRAY_DIR / f"tray-{variant}-{'on' if connected else 'off'}.svg"
