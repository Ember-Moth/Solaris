"""The local database: one SQLite file in the user data directory (docs/storage.md §2).

The schema version is ``PRAGMA user_version``. Opening the database applies the migrations
it has not seen, in order, each in a transaction of its own: a step that fails leaves the
version before it, and the next start tries again.

Every thread shares one connection, one at a time (docs/ui.md §2): the Qt thread and the
worker threads the asyncio loop hands blocking calls to. Statements are short and writes
rare, so waiting for the lock costs nothing noticeable, and a transaction never sees
another thread's statements in the middle of it.

Records carry ``updated_at``, and records that can be deleted carry ``deleted_at`` instead
of going away, so a backup can be merged record by record (storage.md §3). These tombstones
are purged once they are TOMBSTONE_DAYS old.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATABASE_NAME = "solaris.db"
# How long a deleted record stays as a tombstone. A merge cannot tell a record deleted here
# before that from one this machine never had, and brings it back (storage.md §2).
TOMBSTONE_DAYS = 90
# The tables whose records are marked deleted rather than removed.
SOFT_DELETED = ("profiles", "overrides", "plans", "plan_groups", "plan_rules", "backup_targets")

Migration = Callable[[sqlite3.Connection], None]

_log = logging.getLogger(__name__)


class UnusableDatabaseError(Exception):
    """The file is damaged, or a newer version's database this one cannot read."""


# Version 1: the storage layer of M7 (storage.md §2) with the routing tables of M8a
# (routing.md §9). Positions order a list; times are unix seconds; flags are 0 or 1.
_SCHEMA_1 = """
CREATE TABLE profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    in_pool INTEGER NOT NULL DEFAULT 1 CHECK (in_pool IN (0, 1)),
    url TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    auto_update_hours INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
) STRICT;

-- A cache of this machine: the config as last downloaded or imported, and what came with it.
-- No foreign key: the config is written before the profile row that names it. unread: stored
-- while no core could read it; the core reads it at the next connect (D27).
CREATE TABLE profile_contents (
    profile_id TEXT PRIMARY KEY,
    yaml TEXT NOT NULL,
    fetched_at REAL NOT NULL DEFAULT 0,
    upload INTEGER,
    download INTEGER,
    total INTEGER,
    expire INTEGER,
    proxy_count INTEGER,
    unread INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
) STRICT;

-- scope: 'global', or the id of the profile the override belongs to.
CREATE TABLE overrides (
    scope TEXT PRIMARY KEY,
    yaml TEXT NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
) STRICT;

-- One row per setting ever changed; value is JSON. A setting's scope is its field's in
-- models/settings.py, not stored.
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
) STRICT;

-- This machine's own state; value is JSON.
CREATE TABLE state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
) STRICT;

CREATE TABLE backup_targets (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    last_backup_at REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
) STRICT;

-- One row. mode: NULL until chosen, 'profile' or 'plan'; profile_id: the profile in use;
-- info_pattern: NULL for the built-in expression. No foreign keys: the profile and the plan
-- named may be deleted, or purged.
CREATE TABLE routing_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT,
    profile_id TEXT,
    plan_id TEXT,
    info_pattern TEXT,
    updated_at REAL NOT NULL
) STRICT;
INSERT INTO routing_state (id, updated_at) VALUES (1, 0);

-- fallback and plan_rules.target: a group id of the same plan, DIRECT, REJECT, a chained
-- exit (egress:warp) or a mesh outbound (mesh:<profile id>/<name>). egress: the chained
-- exits' settings as JSON (docs/exits.md §7.1).
CREATE TABLE plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    fallback TEXT NOT NULL,
    egress TEXT NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
) STRICT;

-- A group's id is unique within its plan: region groups take theirs from the region code.
-- A plan's groups and rules stay as they were when it is marked deleted, and go when it is
-- purged.
CREATE TABLE plan_groups (
    plan_id TEXT NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    conditions TEXT NOT NULL DEFAULT '{}',
    options TEXT NOT NULL DEFAULT '{}',
    hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    position INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    PRIMARY KEY (plan_id, id)
) STRICT;

CREATE TABLE plan_rules (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    target TEXT NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL
) STRICT;
CREATE INDEX plan_rules_by_plan ON plan_rules (plan_id, position);
"""


def _schema_1(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_1)


MIGRATIONS: tuple[Migration, ...] = (_schema_1,)
SCHEMA_VERSION = len(MIGRATIONS)


class Database:
    def __init__(
        self,
        path: Path,
        *,
        migrations: Sequence[Migration] = MIGRATIONS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Open ``path``, creating it, and migrate it to the last of ``migrations``.

        A file that is not a database, or holds a newer schema than ``migrations``, is set
        aside as ``<name>.<timestamp>.bad`` and an empty database takes its place, as the
        file store did with an index it could not read. ``clock`` dates the records."""
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = _connect(path, migrations)
        except UnusableDatabaseError as error:
            _log.warning("setting %s aside: %s", path, error)
            _set_aside(path, int(clock()))
            connection = _connect(path, migrations)
        self._connection = connection
        # Closes the connection if nobody did, without the ResourceWarning of an open one.
        self._close = weakref.finalize(self, connection.close)

    @property
    def version(self) -> int:
        with self.reading() as connection:
            return user_version(connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The connection inside a write transaction: committed when the block ends, rolled
        back when it raises. Other threads wait until it is over."""
        with self._lock, _transaction(self._connection):
            yield self._connection

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """The connection for statements that only read; other threads wait meanwhile."""
        with self._lock:
            yield self._connection

    def close(self) -> None:
        """Close the connection once a statement in progress is done; later calls raise
        ``sqlite3.ProgrammingError``."""
        with self._lock:
            self._close()


def user_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def read_state(connection: sqlite3.Connection, key: str) -> Any:
    """The value of a ``state`` row; None when there is none."""
    row = connection.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def write_state(connection: sqlite3.Connection, key: str, value: Any, now: float) -> None:
    connection.execute(
        "INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) "
        "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), now),
    )


def purge_tombstones(connection: sqlite3.Connection, now: float) -> int:
    """Remove the records deleted more than TOMBSTONE_DAYS before ``now``; a plan's groups
    and rules go with it. Returns how many went, not counting those."""
    before = now - TOMBSTONE_DAYS * 86_400
    return sum(
        connection.execute(f"DELETE FROM {table} WHERE deleted_at < ?", (before,)).rowcount
        for table in SOFT_DELETED
    )


def sync_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    now: float,
    *,
    parent: tuple[str, str] | None = None,
    created_at: bool = False,
) -> None:
    """Make ``rows`` the records of ``table`` that are not deleted, or of those whose
    ``parent`` column holds the given value: insert the new ones, update the ones that
    changed and mark deleted the ones missing. A row's first column is its key within
    the parent. Only rows that change get a new ``updated_at``; ``created_at`` says the
    table has that column too."""
    key = columns[0]
    where, scope = (f" WHERE {parent[0]} = ?", (parent[1],)) if parent else ("", ())
    selected = ", ".join(columns)
    stored = {
        row[0]: row
        for row in connection.execute(f"SELECT {selected}, deleted_at FROM {table}{where}", scope)
    }
    parent_columns = (parent[0],) if parent else ()
    inserted = (*parent_columns, *columns, *(("created_at",) if created_at else ()), "updated_at")
    insert = (
        f"INSERT INTO {table} ({', '.join(inserted)}) VALUES ({', '.join('?' * len(inserted))})"
    )
    assigned = ", ".join(f"{column} = ?" for column in columns[1:])
    match = f"{key} = ?" + (f" AND {parent[0]} = ?" if parent else "")
    update = f"UPDATE {table} SET {assigned}, updated_at = ?, deleted_at = NULL WHERE {match}"
    for values in rows:
        old = stored.get(values[0])
        if old is None:
            stamps = (now, now) if created_at else (now,)
            connection.execute(insert, (*scope, *values, *stamps))
        elif tuple(old)[1:-1] != tuple(values[1:]) or old[-1] is not None:
            connection.execute(update, (*values[1:], now, values[0], *scope))
    listed = {values[0] for values in rows}
    delete = f"UPDATE {table} SET deleted_at = ?, updated_at = ? WHERE {match}"
    for old_key, old in stored.items():
        if old_key not in listed and old[-1] is None:
            connection.execute(delete, (now, now, old_key, *scope))


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:  # SQLite rolls back by itself after some errors
            connection.execute("ROLLBACK")
        raise


def _connect(path: Path, migrations: Sequence[Migration]) -> sqlite3.Connection:
    # Transactions are explicit (BEGIN ... COMMIT); the lock in Database keeps the threads
    # that share the connection apart.
    connection = sqlite3.connect(path, timeout=5.0, autocommit=True, check_same_thread=False)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        _migrate(connection, migrations)
    except sqlite3.DatabaseError as error:
        connection.close()
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF  # 0 when raised by Python code
        if code in (sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT):
            raise UnusableDatabaseError(str(error)) from error
        raise
    except BaseException:
        connection.close()
        raise
    return connection


def _migrate(connection: sqlite3.Connection, migrations: Sequence[Migration]) -> None:
    while True:
        with _transaction(connection):
            version = user_version(connection)
            if version > len(migrations):
                raise UnusableDatabaseError(
                    f"schema version {version} is newer than {len(migrations)}"
                )
            if version == len(migrations):
                return
            migrations[version](connection)
            connection.execute(f"PRAGMA user_version = {version + 1}")


def _set_aside(path: Path, stamp: int) -> None:
    """Rename the database and its WAL files, which belong to it, out of the way."""
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            path.with_name(path.name + suffix).replace(
                path.with_name(f"{path.name}.{stamp}.bad{suffix}")
            )
