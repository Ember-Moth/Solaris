"""Profiles and overrides in the local database (docs/ui.md §2, docs/storage.md §2).

    profiles           one row per profile, in list order: what the user set
    profile_contents   the config as downloaded or imported, with the time, the usage and the
                       proxy count that came with it; a cache of this machine
    overrides          the global override (scope ``global``) and each profile's own
    routing_state      ``profile_id``: the profile in use (docs/routing.md §9)

Each method is one transaction. ``save`` writes the whole list: a profile missing from it,
or an override cleared, is marked deleted rather than removed; a profile's config is
removed with ``delete_config``, as it is only a cache. The config is written before the
list that names it, so the list never names a profile without one.

"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from solaris.models.database import (
    DATABASE_NAME,
    Database,
    purge_tombstones,
    sync_rows,
)
from solaris.models.profiles import Profile, ProfileError, ProfileList, Usage
from solaris.product import DISPLAY_NAME

GLOBAL_SCOPE = "global"  # the scope of the global override

_log = logging.getLogger(__name__)


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / DISPLAY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / DISPLAY_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / DISPLAY_NAME


def open_database(directory: Path, *, clock: Callable[[], float] = time.time) -> Database:
    """The database in ``directory``, migrated, without old tombstones."""
    database = Database(directory / DATABASE_NAME, clock=clock)
    with database.transaction() as connection:
        purged = purge_tombstones(connection, clock())
        if purged:
            _log.info("purged %d deleted records", purged)
        # Configs left by a crash between writing one and saving the list.
        connection.execute(
            "DELETE FROM profile_contents WHERE profile_id NOT IN "
            "(SELECT id FROM profiles WHERE deleted_at IS NULL)"
        )
    return database


class ProfileStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        """The profiles in the database in ``directory``, opened with ``open_database``.
        ``clock`` dates the records."""
        self.directory = directory
        self.database = open_database(directory, clock=clock)

    def close(self) -> None:
        self.database.close()

    def load(self) -> ProfileList:
        with _stored(), self.database.reading() as connection:
            return load_profiles(connection)

    def save(self, profiles: ProfileList) -> None:
        with _stored(), self.database.transaction() as connection:
            save_profiles(connection, profiles, self.database.clock())

    def read_config(self, profile_id: str) -> str:
        with _stored(), self.database.reading() as connection:
            row = connection.execute(
                "SELECT yaml FROM profile_contents WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            raise ProfileError("config_missing", f"no config stored for {profile_id}")
        return row[0]

    def write_config(self, profile_id: str, text: str, *, keep_read: bool = False) -> None:
        """Store a profile's config. ``keep_read`` keeps the text a core read last, for
        fall_back, when this one is stored unread (D27)."""
        with _stored(), self.database.transaction() as connection:
            write_config(connection, profile_id, text, self.database.clock(), keep_read=keep_read)

    def fall_back(self, profile_id: str) -> bool:
        """Put back the text a core read before an unread one the core then refused; False
        when there is none, as for a profile imported unread."""
        with _stored(), self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE profile_contents SET yaml = read_yaml, read_yaml = NULL, updated_at = ? "
                "WHERE profile_id = ? AND read_yaml IS NOT NULL",
                (self.database.clock(), profile_id),
            )
        return cursor.rowcount > 0

    def delete_config(self, profile_id: str) -> None:
        """Delete a profile's config and its override."""
        with _stored(), self.database.transaction() as connection:
            connection.execute("DELETE FROM profile_contents WHERE profile_id = ?", (profile_id,))
            write_override(connection, profile_id, "", self.database.clock())

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    def read_override(self, profile_id: str | None) -> str:
        """The override's YAML text; empty when there is none."""
        with _stored(), self.database.reading() as connection:
            row = connection.execute(
                "SELECT yaml FROM overrides WHERE scope = ? AND deleted_at IS NULL",
                (_scope(profile_id),),
            ).fetchone()
        return "" if row is None else row[0]

    def write_override(self, profile_id: str | None, text: str) -> None:
        """Store the text as written; blank text deletes the override."""
        with _stored(), self.database.transaction() as connection:
            write_override(connection, profile_id, text, self.database.clock())


@contextmanager
def _stored() -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as error:
        raise ProfileError("storage_failed", str(error)) from error


def _scope(profile_id: str | None) -> str:
    return GLOBAL_SCOPE if profile_id is None else profile_id


def load_profiles(connection: sqlite3.Connection) -> ProfileList:
    rows = connection.execute(
        """
        SELECT p.id, p.name, p.kind, p.url, p.path, p.auto_update_hours, p.in_pool,
               c.fetched_at, c.upload, c.download, c.total, c.expire, c.proxy_count,
               c.has_rules, c.unread,
               o.scope IS NOT NULL AS has_override
        FROM profiles AS p
        LEFT JOIN profile_contents AS c ON c.profile_id = p.id
        LEFT JOIN overrides AS o ON o.scope = p.id AND o.deleted_at IS NULL
        WHERE p.deleted_at IS NULL
        ORDER BY p.position, p.created_at
        """
    ).fetchall()
    profiles = tuple(_profile(row) for row in rows)
    (active,) = connection.execute("SELECT profile_id FROM routing_state").fetchone()
    has_global_override = connection.execute(
        "SELECT 1 FROM overrides WHERE scope = ? AND deleted_at IS NULL", (GLOBAL_SCOPE,)
    ).fetchone()
    return ProfileList(
        profiles,
        active if any(p.id == active for p in profiles) else None,
        has_global_override=has_global_override is not None,
    )


def _profile(row: sqlite3.Row) -> Profile:
    usage = None
    if row["upload"] is not None:
        usage = Usage(row["upload"], row["download"], row["total"], row["expire"])
    return Profile(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        url=row["url"],
        path=row["path"],
        auto_update_hours=row["auto_update_hours"],
        updated_at=row["fetched_at"] or 0.0,
        usage=usage,
        proxy_count=row["proxy_count"],
        has_override=bool(row["has_override"]),
        in_pool=bool(row["in_pool"]),
        has_rules=None if row["has_rules"] is None else bool(row["has_rules"]),
        unread=bool(row["unread"]),
    )


def save_profiles(connection: sqlite3.Connection, profiles: ProfileList, now: float) -> None:
    """Store the list; what came with a profile's config is stored only with the config. A
    profile read by a core no longer needs the text read before it."""
    columns = ("id", "name", "kind", "url", "path", "auto_update_hours", "in_pool", "position")
    rows = [
        (p.id, p.name, p.kind, p.url, p.path, p.auto_update_hours, int(p.in_pool), position)
        for position, p in enumerate(profiles.profiles)
    ]
    sync_rows(connection, "profiles", columns, rows, now, created_at=True)
    for profile in profiles.profiles:
        usage = profile.usage
        fetched = (
            profile.updated_at,
            *((usage.upload, usage.download, usage.total, usage.expire) if usage else [None] * 4),
            profile.proxy_count,
            None if profile.has_rules is None else int(profile.has_rules),
            int(profile.unread),
        )
        connection.execute(
            "UPDATE profile_contents SET fetched_at = ?, upload = ?, download = ?, total = ?, "
            "expire = ?, proxy_count = ?, has_rules = ?, unread = ?, "
            "read_yaml = CASE WHEN ? THEN read_yaml END, updated_at = ? "
            "WHERE profile_id = ? AND "
            "(fetched_at, upload, download, total, expire, proxy_count, has_rules, unread) "
            "IS NOT (?, ?, ?, ?, ?, ?, ?, ?)",
            (*fetched, int(profile.unread), now, profile.id, *fetched),
        )
    connection.execute(
        "UPDATE routing_state SET profile_id = ?, updated_at = ? WHERE profile_id IS NOT ?",
        (profiles.active, now, profiles.active),
    )


def write_config(
    connection: sqlite3.Connection, profile_id: str, text: str, now: float, *, keep_read: bool
) -> None:
    # The text a core read last: the stored one, unless that waits unread itself.
    read = "CASE WHEN unread THEN read_yaml ELSE yaml END" if keep_read else "NULL"
    connection.execute(
        "INSERT INTO profile_contents (profile_id, yaml, updated_at) VALUES (?, ?, ?) "
        f"ON CONFLICT (profile_id) DO UPDATE SET read_yaml = {read}, yaml = excluded.yaml, "
        "updated_at = excluded.updated_at",
        (profile_id, text, now),
    )


def write_override(
    connection: sqlite3.Connection, profile_id: str | None, text: str, now: float
) -> None:
    scope = _scope(profile_id)
    if not text.strip():
        connection.execute(
            "UPDATE overrides SET deleted_at = ?, updated_at = ? "
            "WHERE scope = ? AND deleted_at IS NULL",
            (now, now, scope),
        )
        return
    connection.execute(
        "INSERT INTO overrides (scope, yaml, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (scope) DO UPDATE SET yaml = excluded.yaml, "
        "updated_at = excluded.updated_at, deleted_at = NULL "
        "WHERE overrides.yaml IS NOT excluded.yaml OR overrides.deleted_at IS NOT NULL",
        (scope, text, now),
    )
