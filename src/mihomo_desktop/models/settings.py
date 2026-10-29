"""The user's settings, in ``<data>/settings.json`` (docs/architecture.md §7.6).

One JSON object with a format version. A key that is missing or holds a value this version
does not accept takes its default, and keys it does not know are ignored, so adding a
setting needs no new version. A file that cannot be read at all (not JSON, another version)
is set aside as ``settings.json.<timestamp>.bad`` and the defaults apply.

The file is replaced atomically, like the profile index (models/store.py).
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from mihomo_desktop.host.base import RunOptions
from mihomo_desktop.models.profiles import ProfileError
from mihomo_desktop.models.store import write_atomic

SETTINGS_NAME = "settings.json"
FORMAT_VERSION = 1

LANGUAGES = ("system", "zh", "en")
APPEARANCES = ("system", "light", "dark")
CLOSE_ACTIONS = ("tray", "quit")  # what closing the main window does; the tunnel stays up
DEFAULT_MIXED_PORT = 7890
MIN_MIXED_PORT = 1024
MAX_MIXED_PORT = 65535


class SettingsError(ProfileError):
    """The settings file could not be written (code ``settings_unwritable``)."""


@dataclass(frozen=True)
class Settings:
    language: str = "system"
    appearance: str = "system"
    close_action: str = "tray"
    launch_at_login: bool = False
    # The overview's TUN and system proxy switches: what the next connection asks for.
    tun: bool = False
    system_proxy: bool = False
    mixed_port: int = DEFAULT_MIXED_PORT
    allow_lan: bool = False

    def run_options(self) -> RunOptions:
        return RunOptions(
            tun_enabled=self.tun,
            system_proxy_enabled=self.system_proxy,
            mixed_port=self.mixed_port,
            allow_lan=self.allow_lan,
        )


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
    for field in fields(Settings):
        value = getattr(settings, field.name)
        if not _accepts(field.name, value, getattr(defaults, field.name)):
            raise ValueError(f"invalid {field.name}: {value!r}")
    return settings


def _parse(raw: dict[str, Any]) -> Settings:
    defaults = Settings()
    values = {
        name: raw[name]
        for name in (field.name for field in fields(Settings))
        if name in raw and _accepts(name, raw[name], getattr(defaults, name))
    }
    return Settings(**values)


class SettingsStore:
    """The settings file. ``current`` is what was last loaded or saved; ``update`` writes
    the file first, so ``current`` never shows a value that is not on disk."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / SETTINGS_NAME
        self.current = self.load()

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != FORMAT_VERSION:
                raise ValueError("not a settings file of this version")
        except FileNotFoundError:
            return Settings()
        except OSError, ValueError:
            self._set_aside()
            return Settings()
        return _parse(raw)

    def save(self, settings: Settings) -> None:
        document = {"version": FORMAT_VERSION, **asdict(check(settings))}
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        write_atomic(self.path, text.encode("utf-8"))

    def update(self, **changes: Any) -> Settings:
        """Store ``current`` with ``changes``. Raises ValueError for a value a field does not
        accept and SettingsError when the file cannot be written; ``current`` stays as it was."""
        updated = replace(self.current, **changes)
        if updated != self.current:
            try:
                self.save(updated)
            except OSError as error:
                raise SettingsError("settings_unwritable", str(error)) from error
            self.current = updated
        return self.current

    def _set_aside(self) -> None:
        with contextlib.suppress(OSError):
            self.path.replace(self.path.with_name(f"{SETTINGS_NAME}.{int(time.time())}.bad"))
