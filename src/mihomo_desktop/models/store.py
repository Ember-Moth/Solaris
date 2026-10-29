"""Profiles on disk, in the user data directory (docs/ui.md §2).

    <data>/profiles.json         index: version, active profile, metadata of every profile
    <data>/profiles/<id>.yaml    the config as downloaded or imported
    <data>/override.yaml         the global override, as the user wrote it
    <data>/overrides/<id>.yaml   a profile's own override

Every file is replaced atomically (temporary file, fsync, rename), so a crash leaves
either the old or the new version. The index is written after the config it describes and
before a config is deleted, so it never names a file that is half written. Overrides are
not in the index: a profile has one when its file exists, so saving one is a single write.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mihomo_desktop.models.profiles import Profile, ProfileError, ProfileList, Usage
from mihomo_desktop.product import DISPLAY_NAME

INDEX_NAME = "profiles.json"
CONFIG_DIR = "profiles"
GLOBAL_OVERRIDE_NAME = "override.yaml"
OVERRIDE_DIR = "overrides"
# Version 1 still: the index of the first release also reserved an "override": null field,
# which is ignored on reading and no longer written.
FORMAT_VERSION = 1


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / DISPLAY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / DISPLAY_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / DISPLAY_NAME


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    return Usage(
        upload=int(raw.get("upload", 0)),
        download=int(raw.get("download", 0)),
        total=int(raw.get("total", 0)),
        expire=int(raw.get("expire", 0)),
    )


def _profile(raw: dict[str, Any], has_override: bool) -> Profile:
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
        has_override=has_override,
    )


def _entry(profile: Profile) -> dict[str, Any]:
    entry = asdict(profile)
    del entry["has_override"]  # the override file is the record
    return entry


class ProfileStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @property
    def index_path(self) -> Path:
        return self.directory / INDEX_NAME

    def config_path(self, profile_id: str) -> Path:
        return self.directory / CONFIG_DIR / f"{profile_id}.yaml"

    def load(self) -> ProfileList:
        """Read the index. One that cannot be read is set aside, not overwritten later."""
        has_global_override = self.override_path(None).is_file()
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if raw.get("version") != FORMAT_VERSION:
                raise ValueError(f"unsupported index version {raw.get('version')!r}")
            profiles = tuple(
                _profile(entry, self.override_path(str(entry["id"])).is_file())
                for entry in raw["profiles"]
            )
        except FileNotFoundError:
            return ProfileList(has_global_override=has_global_override)
        except OSError, ValueError, KeyError, TypeError, AttributeError:
            self._set_aside()
            return ProfileList(has_global_override=has_global_override)
        active = raw.get("active")
        if not any(p.id == active for p in profiles):
            active = None
        return ProfileList(profiles, active, has_global_override=has_global_override)

    def save(self, profiles: ProfileList) -> None:
        document = {
            "version": FORMAT_VERSION,
            "active": profiles.active,
            "profiles": [_entry(p) for p in profiles.profiles],
        }
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        write_atomic(self.index_path, text.encode("utf-8"))

    def read_config(self, profile_id: str) -> str:
        try:
            return self.config_path(profile_id).read_text(encoding="utf-8")
        except OSError as error:
            raise ProfileError("config_missing", str(error)) from error

    def write_config(self, profile_id: str, text: str) -> None:
        write_atomic(self.config_path(profile_id), text.encode("utf-8"))

    def delete_config(self, profile_id: str) -> None:
        """Delete a profile's config and its override."""
        for path in (self.config_path(profile_id), self.override_path(profile_id)):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    def override_path(self, profile_id: str | None) -> Path:
        if profile_id is None:
            return self.directory / GLOBAL_OVERRIDE_NAME
        return self.directory / OVERRIDE_DIR / f"{profile_id}.yaml"

    def read_override(self, profile_id: str | None) -> str:
        """The override's YAML text; empty when there is none."""
        try:
            return self.override_path(profile_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as error:
            raise ProfileError("override_unreadable", str(error)) from error

    def write_override(self, profile_id: str | None, text: str) -> None:
        """Store the text as written; blank text removes the override."""
        path = self.override_path(profile_id)
        if text.strip():
            write_atomic(path, text.encode("utf-8"))
        else:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def _set_aside(self) -> None:
        with contextlib.suppress(OSError):
            self.index_path.replace(
                self.index_path.with_name(f"{INDEX_NAME}.{int(time.time())}.bad")
            )
