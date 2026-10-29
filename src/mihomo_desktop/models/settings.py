"""The user's settings (docs/ui.md §8).

Code depends on the ``SettingsStore`` interface only: read ``current``, write with
``update``, and ``subscribe`` to hear of changes. ``JsonSettingsStore`` is a stand-in that
keeps them in ``<data>/settings.json`` until the local data moves to SQLite; its file format
is not meant to last.

Every setting names its scope in the field's metadata: ``synced`` settings follow the user
to other devices with the backup, ``local`` ones only mean something on this machine.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Protocol

from mihomo_desktop.host.base import RunOptions
from mihomo_desktop.models.profiles import ProfileError
from mihomo_desktop.models.store import write_atomic

LANGUAGES = ("system", "zh", "en")
APPEARANCES = ("system", "light", "dark")
CLOSE_ACTIONS = ("tray", "quit")  # what closing the main window does; the tunnel stays up
DEFAULT_MIXED_PORT = 7890
MIN_MIXED_PORT = 1024
MAX_MIXED_PORT = 65535

SYNCED = "synced"  # the user's preference, the same on every device
LOCAL = "local"  # about this machine: its system, its network, its ports


def _setting(default: Any, scope: str) -> Any:
    return field(default=default, metadata={"scope": scope})


@dataclass(frozen=True)
class Settings:
    language: str = _setting("system", SYNCED)
    appearance: str = _setting("system", SYNCED)
    # The desktop's conventions differ between machines (tray, menu bar).
    close_action: str = _setting("tray", LOCAL)
    # Mirrors this machine's login item; the system is the record.
    launch_at_login: bool = _setting(False, LOCAL)
    # The overview's TUN and system proxy switches: what the next connection asks for. Both
    # take over this machine's traffic, which another machine must not do on restore.
    tun: bool = _setting(False, LOCAL)
    system_proxy: bool = _setting(False, LOCAL)
    # A port free here may be taken elsewhere; LAN access exposes it to this network.
    mixed_port: int = _setting(DEFAULT_MIXED_PORT, LOCAL)
    allow_lan: bool = _setting(False, LOCAL)

    def run_options(self) -> RunOptions:
        return RunOptions(
            tun_enabled=self.tun,
            system_proxy_enabled=self.system_proxy,
            mixed_port=self.mixed_port,
            allow_lan=self.allow_lan,
        )


def scope(name: str) -> str:
    """``SYNCED`` or ``LOCAL``: whether a backup restores the setting on another device."""
    return next(f.metadata["scope"] for f in fields(Settings) if f.name == name)


class SettingsError(ProfileError):
    """The settings could not be written (code ``settings_unwritable``)."""


# Called with the settings before and after a change, on the thread that wrote them.
Listener = Callable[[Settings, Settings], None]


class SettingsStore(Protocol):
    @property
    def current(self) -> Settings: ...

    def update(self, **changes: Any) -> Settings:
        """Write ``changes`` and tell the listeners. Raises ValueError for a value a setting
        does not accept and SettingsError when they cannot be written; then ``current``
        stays as it was and nobody is told."""
        ...

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` after every change; returns the call that stops it."""
        ...


_CHOICES: dict[str, tuple[str, ...]] = {
    "language": LANGUAGES,
    "appearance": APPEARANCES,
    "close_action": CLOSE_ACTIONS,
}


def valid_port(port: object) -> bool:
    return (
        isinstance(port, int)
        and not isinstance(port, bool)
        and MIN_MIXED_PORT <= port <= MAX_MIXED_PORT
    )


def _accepts(name: str, value: object, default: object) -> bool:
    if name in _CHOICES:
        return value in _CHOICES[name]
    if name == "mixed_port":
        return valid_port(value)
    return type(value) is type(default)


def check(settings: Settings) -> Settings:
    """``settings`` if every field holds a value it accepts; raises ValueError otherwise."""
    defaults = Settings()
    for item in fields(Settings):
        value = getattr(settings, item.name)
        if not _accepts(item.name, value, getattr(defaults, item.name)):
            raise ValueError(f"invalid {item.name}: {value!r}")
    return settings


class Notifier:
    """The listeners of a SettingsStore implementation."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def notify(self, before: Settings, after: Settings) -> None:
        for listener in list(self._listeners):
            listener(before, after)


SETTINGS_NAME = "settings.json"


class JsonSettingsStore:
    """Temporary: the settings as one JSON object in ``settings.json``, replaced atomically.
    A missing or unacceptable value takes its default; a file that cannot be read is set
    aside as ``settings.json.<timestamp>.bad``."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / SETTINGS_NAME
        self._notifier = Notifier()
        self._current = self._load()

    @property
    def current(self) -> Settings:
        return self._current

    def update(self, **changes: Any) -> Settings:
        before = self._current
        after = check(replace(before, **changes))
        if after == before:
            return before
        text = json.dumps(asdict(after), ensure_ascii=False, indent=2) + "\n"
        try:
            write_atomic(self.path, text.encode("utf-8"))
        except OSError as error:
            raise SettingsError("settings_unwritable", str(error)) from error
        self._current = after
        self._notifier.notify(before, after)
        return after

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        return self._notifier.subscribe(listener)

    def _load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            return Settings()
        except OSError, ValueError:
            with contextlib.suppress(OSError):
                self.path.replace(self.path.with_name(f"{SETTINGS_NAME}.{int(time.time())}.bad"))
            return Settings()
        defaults = Settings()
        values = {
            name: raw[name]
            for name in (item.name for item in fields(Settings))
            if name in raw and _accepts(name, raw[name], getattr(defaults, name))
        }
        return Settings(**values)
