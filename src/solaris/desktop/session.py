"""Whether a request to quit is the system's, at the end of the session, or the user's.

Qt reports both as a Quit event. On macOS ⌘Q, the Dock's Quit and the system's quit as it
logs out, restarts or shuts down all reach NSApplication's ``terminate:``; only the system's
Apple Event says why (kAEQuitReason). Elsewhere every Quit event is the system's: Windows sends
one as the session ends (WM_ENDSESSION), and the tray's 退出 Solaris does not go through Qt.
"""

from __future__ import annotations

import ctypes
import sys

from solaris.desktop.titlebar import ObjC

KEY_QUIT_REASON = int.from_bytes(b"why?")  # kAEQuitReason, an Apple Event attribute


def session_ending(platform: str = sys.platform) -> bool:
    return platform != "darwin" or mac_quit_reason() is not None


def mac_quit_reason() -> int | None:
    """The reason the quit Apple Event being handled gives, such as ``logo`` for a logout;
    None when none is being handled or it gives none."""
    try:
        objc = ObjC()
    except OSError:
        return None
    with objc.pool():
        manager = objc.send(
            ctypes.c_void_p, objc.cls(b"NSAppleEventManager"), b"sharedAppleEventManager"
        )
        return quit_reason(objc, objc.send(ctypes.c_void_p, manager, b"currentAppleEvent"))


def quit_reason(objc: ObjC, event: int | None) -> int | None:
    """``event``'s kAEQuitReason as a four-character code; 0 for one that is no code."""
    if not event:
        return None
    reason = objc.send(
        ctypes.c_void_p,
        event,
        b"attributeDescriptorForKeyword:",
        KEY_QUIT_REASON,
        argtypes=(ctypes.c_uint32,),
    )
    if not reason:
        return None
    return objc.send(ctypes.c_uint32, reason, b"typeCodeValue")
