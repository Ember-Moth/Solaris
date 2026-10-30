"""The user's settings (docs/ui.md §8).

Code depends on the ``SettingsStore`` interface only: read ``current``, write with
``update``, and ``subscribe`` to hear of changes. ``DatabaseSettingsStore`` keeps them in
the local database (database.py).

Every setting names its scope in the field's metadata: ``synced`` settings follow the user
to other devices with the backup, ``local`` ones only mean something on this machine.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any, Protocol

from solaris.host.base import RunOptions
from solaris.models.database import Database
from solaris.models.profiles import ProfileError

LANGUAGES = ("system", "zh", "en")
APPEARANCES = ("system", "light", "dark")
CLOSE_ACTIONS = ("tray", "quit")  # what closing the main window does; the tunnel stays up
# How often the UI looks for a new release (docs/release.md §2): every day, 3 days or week.
UPDATE_CHECK_HOURS = (24, 72, 168)
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
    # Look for a new release at startup and then every so many hours.
    update_check: bool = _setting(True, SYNCED)
    update_check_hours: int = _setting(UPDATE_CHECK_HOURS[0], SYNCED)

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
    if name == "update_check_hours":
        return type(value) is int and value in UPDATE_CHECK_HOURS
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


class DatabaseSettingsStore:
    """The settings as rows of the database's ``settings`` table: one per setting that was
    ever changed, the value as JSON beside the setting's scope. A setting without a row,
    or with a value it does not accept, takes its default.

    The app keeps one store per database. Code that writes the table itself, such as a
    restore, calls ``reload`` afterwards so the listeners hear of it."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._notifier = Notifier()
        self._lock = threading.Lock()  # one change at a time, from any thread
        with database.reading() as connection:
            self._current = read_settings(connection)

    @property
    def current(self) -> Settings:
        return self._current

    def update(self, **changes: Any) -> Settings:
        with self._lock:
            before = self._current
            after = check(replace(before, **changes))
            if after == before:
                return before
            changed = {
                item.name: getattr(after, item.name)
                for item in fields(Settings)
                if getattr(after, item.name) != getattr(before, item.name)
            }
            try:
                with self._database.transaction() as connection:
                    write_settings(connection, changed, self._database.clock())
            except sqlite3.Error as error:
                raise SettingsError("settings_unwritable", str(error)) from error
            self._current = after
        self._notifier.notify(before, after)
        return after

    def reload(self) -> Settings:
        """Read the table again and tell the listeners what changed."""
        with self._lock:
            before = self._current
            with self._database.reading() as connection:
                after = read_settings(connection)
            self._current = after
        if after != before:
            self._notifier.notify(before, after)
        return after

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        return self._notifier.subscribe(listener)


def accepted(raw: Mapping[str, object]) -> dict[str, Any]:
    """The settings in ``raw`` whose values they accept; anything else is left out."""
    defaults = Settings()
    return {
        item.name: raw[item.name]
        for item in fields(Settings)
        if item.name in raw and _accepts(item.name, raw[item.name], getattr(defaults, item.name))
    }


def read_settings(connection: sqlite3.Connection) -> Settings:
    raw: dict[str, object] = {}
    for key, value in connection.execute("SELECT key, value FROM settings"):
        with contextlib.suppress(ValueError):
            raw[key] = json.loads(value)
    return Settings(**accepted(raw))


def write_settings(
    connection: sqlite3.Connection, values: Mapping[str, Any], now: float, *, keep: bool = False
) -> None:
    """Store checked values with their scopes; ``keep`` leaves the ones already stored."""
    if keep:
        conflict = "NOTHING"
    else:
        conflict = (
            "UPDATE SET value = excluded.value, scope = excluded.scope, "
            "updated_at = excluded.updated_at"
        )
    connection.executemany(
        "INSERT INTO settings (key, value, scope, updated_at) VALUES (?, ?, ?, ?) "
        f"ON CONFLICT (key) DO {conflict}",
        [(name, json.dumps(value), scope(name), now) for name, value in values.items()],
    )
