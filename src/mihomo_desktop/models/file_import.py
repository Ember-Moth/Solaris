"""The file store of earlier versions, read once to be imported into the database.

    <data>/profiles.json         index: version 1, the active profile, every profile's metadata
    <data>/profiles/<id>.yaml    the config as downloaded or imported
    <data>/override.yaml         the global override
    <data>/overrides/<id>.yaml   a profile's own override
    <data>/settings.json         the settings, one JSON object

``read_files`` reads them as the file store did: a missing file, or one that is damaged or
of an unknown format, adds nothing. Once the import is stored, ``set_aside`` renames every
file it read with the suffix ``.migrated``; nothing is deleted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mihomo_desktop.models.profiles import Profile, ProfileList, Usage
from mihomo_desktop.models.settings import Settings, accepted

INDEX_NAME = "profiles.json"
CONFIG_DIR = "profiles"
GLOBAL_OVERRIDE_NAME = "override.yaml"
OVERRIDE_DIR = "overrides"
SETTINGS_NAME = "settings.json"
# The index of the first release also had an "override": null field in each profile.
INDEX_VERSION = 1
MIGRATED_SUFFIX = ".migrated"


@dataclass(frozen=True)
class FileStore:
    profiles: ProfileList = field(default_factory=ProfileList)
    configs: Mapping[str, str] = field(default_factory=dict)  # profile id -> config
    overrides: Mapping[str | None, str] = field(default_factory=dict)  # None: the global one
    settings: Mapping[str, Any] = field(default_factory=dict)  # those not at their default
    files: tuple[str, ...] = ()  # every file read, relative to the data directory


def read_files(directory: Path) -> FileStore:
    """What the file store in ``directory`` holds. Raises OSError when a file cannot be
    read for another reason than its absence, so the import can be tried again."""
    files = [
        *_present(directory, INDEX_NAME),
        *_listed(directory, CONFIG_DIR),
        *_present(directory, GLOBAL_OVERRIDE_NAME),
        *_listed(directory, OVERRIDE_DIR),
        *_present(directory, SETTINGS_NAME),
    ]
    if not files:
        return FileStore()
    profiles = _read_index(directory / INDEX_NAME)
    configs: dict[str, str] = {}
    overrides: dict[str | None, str] = {}
    for profile in profiles.profiles:
        if (text := _read_text(directory / CONFIG_DIR / f"{profile.id}.yaml")) is not None:
            configs[profile.id] = text
        own = _read_text(directory / OVERRIDE_DIR / f"{profile.id}.yaml")
        if own is not None and own.strip():
            overrides[profile.id] = own
    shared = _read_text(directory / GLOBAL_OVERRIDE_NAME)
    if shared is not None and shared.strip():
        overrides[None] = shared
    settings = _read_settings(directory / SETTINGS_NAME)
    return FileStore(profiles, configs, overrides, settings, tuple(files))


def set_aside(directory: Path, files: Iterable[str]) -> list[str]:
    """Rename each file to ``<name>.migrated`` (``.migrated.2`` and so on when taken);
    returns the files that could not be renamed."""
    left: list[str] = []
    for name in files:
        path = directory / name
        if not path.exists():
            continue
        try:
            path.rename(_free(path.with_name(path.name + MIGRATED_SUFFIX)))
        except OSError:
            left.append(name)
    return left


def _present(directory: Path, name: str) -> list[str]:
    return [name] if (directory / name).exists() else []


def _listed(directory: Path, folder: str) -> list[str]:
    return sorted(f"{folder}/{path.name}" for path in (directory / folder).glob("*.yaml"))


def _free(path: Path) -> Path:
    candidate, number = path, 1
    while candidate.exists():
        number += 1
        candidate = path.with_name(f"{path.name}.{number}")
    return candidate


def _read_text(path: Path) -> str | None:
    """The file's text; None when it is missing or not UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError, UnicodeDecodeError:
        return None


def _read_json(path: Path) -> Any:
    text = _read_text(path)
    try:
        return None if text is None else json.loads(text)
    except ValueError:
        return None


def _read_index(path: Path) -> ProfileList:
    raw = _read_json(path)
    try:
        if raw.get("version") != INDEX_VERSION:
            return ProfileList()
        read = [_profile(entry) for entry in raw["profiles"]]
    except ValueError, KeyError, TypeError, AttributeError:
        return ProfileList()
    first: dict[str, Profile] = {}
    for profile in read:
        first.setdefault(profile.id, profile)
    profiles = tuple(first.values())
    active = raw.get("active")
    return ProfileList(profiles, active if any(p.id == active for p in profiles) else None)


def _profile(raw: dict[str, Any]) -> Profile:
    count = raw.get("proxy_count")
    return Profile(
        id=str(raw["id"]),
        name=str(raw["name"]),
        kind=str(raw["kind"]),
        url=str(raw.get("url", "")),
        path=str(raw.get("path", "")),
        auto_update_hours=int(raw.get("auto_update_hours", 0)),
        updated_at=float(raw.get("updated_at", 0)),
        usage=_usage(raw.get("usage")),
        proxy_count=int(count) if count is not None else None,
    )


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    return Usage(
        upload=int(raw.get("upload", 0)),
        download=int(raw.get("download", 0)),
        total=int(raw.get("total", 0)),
        expire=int(raw.get("expire", 0)),
    )


def _read_settings(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return {}
    defaults = Settings()
    return {
        name: value for name, value in accepted(raw).items() if value != getattr(defaults, name)
    }
