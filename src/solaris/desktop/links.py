"""Install links: a provider's 一键导入 button (docs/release.md §7, decision D6).

``solaris://install-config?url=<subscription>&name=<name>``, or the Clash clients' common
``clash://install-config?url=…``, fills in the import form; the user imports from there.
Any web page can open such a link, so it never imports by itself.

A link arrives as a command-line argument on Windows (the installer registers the scheme),
as an Apple Event on macOS, which Qt turns into a QFileOpenEvent on the application, or
from a second UI that hands it to the first (instance.py).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtGui import QFileOpenEvent

from solaris.models.profiles import ProfileError
from solaris.models.subscription import check_url

SCHEMES = ("solaris", "clash")
ACTION = "install-config"
MAX_NAME = 100
MAX_LINK = 8192  # longer is no subscription link


@dataclass(frozen=True)
class InstallLink:
    url: str  # the subscription, http or https
    name: str  # may be empty


def is_link(text: str) -> bool:
    """Whether ``text`` uses one of the schemes; whether it is a valid one is ``parse``'s."""
    scheme, separator, _ = text.partition(":")
    return bool(separator) and scheme.lower() in SCHEMES


def parse(text: str) -> InstallLink | None:
    """The subscription a link names; None for anything else."""
    if len(text) > MAX_LINK or not is_link(text):
        return None
    parts = urlsplit(text.strip())
    if parts.netloc.lower() != ACTION or parts.path not in ("", "/"):
        return None
    query = parse_qs(parts.query)
    urls = query.get("url") or []
    if len(urls) != 1:
        return None
    try:
        url = check_url(urls[0])
    except ProfileError:
        return None
    name = "".join(c for c in (query.get("name") or [""])[0] if unicodedata.category(c)[0] != "C")
    return InstallLink(url, name.strip()[:MAX_NAME].strip())


def from_arguments(arguments: Sequence[str]) -> str | None:
    """The first command-line argument that is a link, as Windows passes one."""
    return next((a for a in arguments if is_link(a) and len(a) <= MAX_LINK), None)


class LinkReceiver(QObject):
    """Takes links from the system (QFileOpenEvent) and from wherever else they arrive, and
    hands them on once the UI is ready: a link that opened the app comes before its window."""

    received = Signal(str)

    def __init__(self, app: QCoreApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: list[str] = []
        self._ready = False
        app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if event.type() == QEvent.Type.FileOpen and isinstance(event, QFileOpenEvent):
            link = bytes(event.url().toEncoded().data()).decode("utf-8", "replace")
            if is_link(link):
                self.deliver(link)
                return True
        return super().eventFilter(watched, event)

    def deliver(self, link: str) -> None:
        if self._ready:
            self.received.emit(link)
        else:
            self._pending.append(link)

    def ready(self) -> None:
        """The UI can take links now; the ones that came early go first."""
        self._ready = True
        pending, self._pending = self._pending, []
        for link in pending:
            self.received.emit(link)
