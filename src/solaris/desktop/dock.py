"""macOS: Solaris in the Dock while its window shows, in the menu bar only while it hides
(docs/ui.md §8, "关闭窗口").

While the main window hides, Solaris is an accessory app: neither the Dock nor the app
switcher (⌘Tab) lists it, and the menu bar's icon is the way back. Showing the window makes it
a regular app again and activates it; the window alone would come up with the menu bar still
the app's in front.

Qt has no call for the activation policy, so it goes through the Objective-C runtime with
desktop/titlebar.py's ObjC, which works the same in a development run and in Solaris.app.
DesktopViewModel decides when, from the window's visibility; other Qt platforms, such as the
tests' offscreen one, make no calls.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import MutableMapping
from typing import Protocol

from solaris.desktop.titlebar import ObjC

# Qt's cocoa plugin, when set, neither makes the process a regular app as the application is
# created nor activates it as it finishes launching. Qt reads it again after the event loop
# has started, so it stays set.
QT_NO_FOREGROUND_ENV = "QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"

REGULAR = "regular"  # in the Dock and ⌘Tab, with a menu bar
ACCESSORY = "accessory"  # neither; activated only on request

# NSApplicationActivationPolicy
_POLICIES = {REGULAR: 0, ACCESSORY: 1}


class Application(Protocol):
    """What DesktopViewModel asks of NSApp; tests pass a stand-in."""

    def set_policy(self, policy: str) -> None: ...
    def activate(self) -> None: ...


class MacApplication:
    """NSApp's activation policy, and activating it (macOS 14's ``activate``; Solaris needs
    macOS 26)."""

    def __init__(self) -> None:
        self._objc = ObjC()

    def _app(self) -> int:
        return self._objc.send(
            ctypes.c_void_p, self._objc.cls(b"NSApplication"), b"sharedApplication"
        )

    def set_policy(self, policy: str) -> None:
        with self._objc.pool():
            self._objc.send(
                ctypes.c_bool,
                self._app(),
                b"setActivationPolicy:",
                _POLICIES[policy],
                argtypes=(ctypes.c_long,),
            )

    def activate(self) -> None:
        with self._objc.pool():
            self._objc.send(None, self._app(), b"activate")


def open_in_menu_bar(
    environ: MutableMapping[str, str] = os.environ, platform: str = sys.platform
) -> None:
    """Before the application is created, when Solaris opens into the menu bar (``--background``):
    Qt asks to activate the app as it finishes launching, which macOS grants an accessory app,
    and Qt reports that activation as it reports a reopen, so the window would show
    (DesktopViewModel.reopened). DesktopViewModel makes Solaris an accessory app as it starts,
    and a regular one when the window shows. The UI's child processes (codesign, schtasks) are
    not Qt apps."""
    if platform == "darwin":
        environ[QT_NO_FOREGROUND_ENV] = "1"
