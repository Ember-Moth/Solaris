"""What the main window gives back while it hides in the tray or the menu bar (docs/ui.md §8,
"隐藏时释放").

A moment after the window hides, Main.qml lets its pages go and asks DesktopViewModel to
release the window; this gives back what only drawing it took. Showing the window loads the
pages again, and Qt sets up its graphics anew for the first frame. The ViewModels, the tray
icon and the dialogs that bring the window back stay as they are.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPixmapCache
from PySide6.QtQml import qmlEngine
from PySide6.QtQuick import QQuickWindow


def release(window: QQuickWindow, *, native: bool) -> None:
    """``window`` hides, and its pages were let go just now. ``native``: give up the native
    window as well; Qt creates it again as the window shows. On macOS its Metal layer keeps
    the frames it drew until then, 30 to 50 MB; Windows keeps it, since its frame is set up on
    the native window once (desktop/titlebar.py)."""
    # The pages' items wait for the event loop to delete them; the collections below then
    # find what only they used.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    engine = qmlEngine(window)
    if engine is not None:
        engine.collectGarbage()
    gc.collect()
    QPixmapCache.clear()
    # The scene graph, its glyph and texture caches and the graphics device, and Qt Quick's
    # cache of images no item shows. They persist again afterwards, so that a window hidden
    # and shown again before its next release keeps them.
    window.setPersistentSceneGraph(False)
    window.setPersistentGraphics(False)
    window.releaseResources()
    window.setPersistentGraphics(True)
    window.setPersistentSceneGraph(True)
    if native:
        window.destroy()  # TitleBar sets up the new one as it did this one
