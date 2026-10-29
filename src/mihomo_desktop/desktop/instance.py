"""One UI per user (docs/architecture.md §7.6).

The first UI holds a lock file in the user's data directory and listens on a local server.
A later one finds the lock taken, asks the first to show its window, and exits. The lock
decides, not the server: QLockFile notices a lock left by a process that died, and two UIs
started at once cannot both take it. The server's name is derived from the user and the
lock's directory, so other users and other data directories (the preview) have their own.
"""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from mihomo_desktop.product import PRODUCT_ID

LOCK_NAME = "ui.lock"
ACTIVATE = b"activate\n"
CONNECT_TIMEOUT_MS = 500
# The first UI takes the lock before it listens; give it this long to start listening.
CONNECT_ATTEMPTS = 6


def server_name(directory: Path, user: str | None = None) -> str:
    """Unique per user and data directory; Windows pipe names are visible to every user."""
    user = getpass.getuser() if user is None else user
    digest = hashlib.sha256(f"{user}\0{directory.resolve()}".encode()).hexdigest()
    return f"{PRODUCT_ID}-{digest[:16]}"


def _allow_foreground() -> None:
    """Let the first UI bring its window to the front. Windows only lets the foreground
    process do that, and this one is: the user just started it."""
    if sys.platform == "win32":
        asfw_any = -1
        ctypes.windll.user32.AllowSetForegroundWindow(asfw_any)


class SingleInstance(QObject):
    activated = Signal()  # another UI started and asked for the window

    def __init__(self, directory: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        directory.mkdir(parents=True, exist_ok=True)
        self.name = server_name(directory)
        self._lock = QLockFile(str(directory / LOCK_NAME))
        # A UI runs for days; only a lock whose process is gone is stale.
        self._lock.setStaleLockTime(0)
        self._server: QLocalServer | None = None

    def claim(self) -> bool:
        """True when this is the only UI, which then listens for the next ones. False when
        another UI runs; it has been asked to show its window, and this one should exit."""
        if self._lock.tryLock(0):
            self._listen()
            return True
        self._notify()
        return False

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _listen(self) -> None:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        # A UI that crashed leaves its socket file behind on macOS; the lock says it is gone.
        QLocalServer.removeServer(self.name)
        server.newConnection.connect(self._accept)
        if server.listen(self.name):
            self._server = server

    def _accept(self) -> None:
        if self._server is None:
            return
        # The server owns the sockets; a slot that captured one would outlive it.
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.readyRead.connect(self._read)
            socket.disconnected.connect(socket.deleteLater)

    def _read(self) -> None:
        socket = self.sender()
        if not isinstance(socket, QLocalSocket) or not socket.canReadLine():
            return  # the rest of the line is on its way
        if bytes(socket.readLine().data()) == ACTIVATE:
            self.activated.emit()
        socket.disconnectFromServer()

    def _notify(self) -> None:
        _allow_foreground()
        for attempt in range(CONNECT_ATTEMPTS):
            if attempt:
                QThread.msleep(CONNECT_TIMEOUT_MS)
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(CONNECT_TIMEOUT_MS):
                socket.write(ACTIVATE)
                socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
                socket.disconnectFromServer()
                return
