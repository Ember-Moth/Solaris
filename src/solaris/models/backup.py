"""Backup snapshots (docs/storage.md §3): the user's data in the database as one JSON
document, to keep in a file or upload to a backup target, and its restore.

A snapshot holds what the user made, nothing of this machine:

    profiles   subscriptions with their link and update setting, not what was downloaded;
               local files with the config as imported (``config``)
    overrides  the global override and each profile's own
    routing    the routing mode, the profile and the plan in use, the info pattern, and
               every plan with its groups and rules
    settings   the settings whose scope is ``synced``

Usage, cached downloads, local settings, tokens and the WARP account stay out. Records keep
their times. A deleted record is a tombstone, its key and times without what it held, so a
removed subscription's link does not live on in the backups.

A restore either replaces the local data, as if the user had just made it so, or merges
it record by record: whichever side changed a record later wins, tombstones included.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import platform
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from solaris import __version__
from solaris.models.database import Database
from solaris.models.profiles import KIND_FILE, KIND_SUBSCRIPTION, ProfileError
from solaris.models.routing import GROUP_KINDS, MODES, RULE_KINDS
from solaris.models.settings import SYNCED, Settings, accepted, scope

FORMAT = "solaris-backup"
VERSION = 1
REPLACE = "replace"  # the local data becomes the snapshot's
MERGE = "merge"  # record by record, the later change wins
RESTORE_MODES = (MERGE, REPLACE)
# A file holds a few configs of at most 15 MiB each (subscription.MAX_CONFIG_BYTES).
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
# Written as the snapshot's header, so they are no change of its content.
_HEADER = ("format", "version", "created_at", "device", "app_version")


class BackupError(ProfileError):
    """A file or a download is no snapshot this version can restore: ``not_a_backup``,
    ``newer_backup`` or ``too_large``; or a provider failed: ``not_signed_in``,
    ``backup_missing``, ``cloud_full``, ``cloud_unreachable``, ``cloud_failed``."""


@dataclass(frozen=True)
class Counts:
    profiles: int
    plans: int


@dataclass(frozen=True)
class SnapshotInfo:
    """What a list of snapshots shows of each."""

    created_at: float
    device: str
    app_version: str
    counts: Counts


@dataclass(frozen=True)
class Snapshot:
    document: dict[str, Any]

    @property
    def info(self) -> SnapshotInfo:
        document = self.document
        profiles = sum(not _deleted(p) for p in document["profiles"])
        plans = sum(not _deleted(p) for p in document["routing"]["plans"])
        return SnapshotInfo(
            document["created_at"],
            document["device"],
            document["app_version"],
            Counts(profiles, plans),
        )

    @property
    def empty(self) -> bool:
        """Whether it holds nothing the user made, as from a database just created."""
        document = self.document
        routing = document["routing"]
        records = (document["profiles"], document["overrides"], routing["plans"])
        return not any(records) and not document["settings"] and routing["updated_at"] == 0

    @property
    def digest(self) -> str:
        """The hash of the content, without the header: an unchanged database gives the
        same digest whenever and wherever it is taken."""
        content = {k: v for k, v in self.document.items() if k not in _HEADER}
        return hashlib.sha256(_canonical(content).encode()).hexdigest()

    def encode(self) -> bytes:
        text = json.dumps(self.document, ensure_ascii=False, indent=2)
        return (text + "\n").encode()

    @staticmethod
    def decode(data: bytes) -> Snapshot:
        """The snapshot in ``data``; raises BackupError for anything else."""
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise BackupError("too_large", f"more than {MAX_SNAPSHOT_BYTES} bytes")
        try:
            document = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as error:
            raise BackupError("not_a_backup", str(error)) from error
        if not isinstance(document, dict) or document.get("format") != FORMAT:
            raise BackupError("not_a_backup", f"format is not {FORMAT}")
        version = document.get("version")
        if type(version) is not int or version < 1:
            raise BackupError("not_a_backup", f"version {version!r}")
        if version > VERSION:
            raise BackupError("newer_backup", f"version {version} is newer than {VERSION}")
        _check_document(document)
        return Snapshot(document)


def device_name() -> str:
    """The name this machine goes by, as snapshots name where they come from."""
    return platform.node().removesuffix(".local") or "?"


class Backups:
    """Snapshots of a database and their restore; every call is one read or transaction."""

    def __init__(
        self, database: Database, *, device: str | None = None, app_version: str = __version__
    ) -> None:
        self._database = database
        self._device = device_name() if device is None else device
        self._app_version = app_version

    def snapshot(self) -> Snapshot:
        with _stored(), self._database.reading() as connection:
            return export(connection, self._database.clock(), self._device, self._app_version)

    def counts(self) -> Counts:
        """The profiles and plans the database holds now."""
        with _stored(), self._database.reading() as connection:
            return counts(connection)

    def restore(self, snapshot: Snapshot, mode: str) -> None:
        """Write the snapshot's data with ``mode``; the profiles, the routing and the
        settings read from the database are stale afterwards."""
        with _stored(), self._database.transaction() as connection:
            restore(connection, snapshot, mode, self._database.clock())


@contextmanager
def _stored() -> Iterator[None]:
    """Database failures as the store reports them (``storage_failed``)."""
    try:
        yield
    except sqlite3.Error as error:
        raise ProfileError("storage_failed", str(error)) from error


def write_file(path: Path, snapshot: Snapshot) -> None:
    """Write the snapshot to ``path`` whole or not at all."""
    partial = path.with_name(path.name + ".part")
    try:
        partial.write_bytes(snapshot.encode())
        partial.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()


def read_file(path: Path) -> Snapshot:
    """The snapshot in ``path``; OSError when it cannot be read, BackupError when it is no
    snapshot."""
    with path.open("rb") as file:
        data = file.read(MAX_SNAPSHOT_BYTES + 1)
    return Snapshot.decode(data)


# -- export ------------------------------------------------------------------------------------
def counts(connection: sqlite3.Connection) -> Counts:
    (profiles,) = connection.execute(
        "SELECT COUNT(*) FROM profiles WHERE deleted_at IS NULL"
    ).fetchone()
    (plans,) = connection.execute("SELECT COUNT(*) FROM plans WHERE deleted_at IS NULL").fetchone()
    return Counts(profiles, plans)


def export(connection: sqlite3.Connection, now: float, device: str, app_version: str) -> Snapshot:
    """The snapshot of the database as it is; records in a fixed order, so the same data
    always gives the same document."""
    state = connection.execute(
        "SELECT mode, profile_id, plan_id, info_pattern, updated_at FROM routing_state"
    ).fetchone()
    document = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": now,
        "device": device,
        "app_version": app_version,
        "profiles": _export_profiles(connection),
        "overrides": [
            _record(row, ("scope", "yaml"))
            for row in connection.execute("SELECT * FROM overrides ORDER BY scope")
        ],
        "routing": {**dict(state), "plans": _export_plans(connection)},
        "settings": [
            {"key": key, "value": json.loads(value), "updated_at": updated_at}
            for key, value, updated_at in connection.execute(
                "SELECT key, value, updated_at FROM settings ORDER BY key"
            )
            if key in _SYNCED_SETTINGS
        ],
    }
    return Snapshot(document)


def _record(row: sqlite3.Row, columns: Sequence[str], **values: Any) -> dict[str, Any]:
    """A live row's columns and ``updated_at``, or the tombstone of a deleted one."""
    key = columns[0]
    if row["deleted_at"] is not None:
        return {key: row[key], "updated_at": row["updated_at"], "deleted_at": row["deleted_at"]}
    return {**{c: row[c] for c in columns}, **values, "updated_at": row["updated_at"]}


def _export_profiles(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    profiles = []
    for row in connection.execute(
        "SELECT p.*, c.yaml, c.updated_at AS config_updated_at FROM profiles AS p "
        "LEFT JOIN profile_contents AS c ON c.profile_id = p.id AND p.kind = ? "
        "ORDER BY p.deleted_at IS NOT NULL, p.position, p.created_at, p.id",
        (KIND_FILE,),
    ):
        extra: dict[str, Any] = {"in_pool": bool(row["in_pool"])}
        if row["yaml"] is not None:  # a subscription's download stays here
            extra["config"] = {"yaml": row["yaml"], "updated_at": row["config_updated_at"]}
        columns = ("id", "name", "kind", "url", "path", "auto_update_hours", "position")
        profiles.append(_record(row, (*columns, "created_at"), **extra))
    return profiles


def _export_plans(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    plans = []
    for row in connection.execute(
        "SELECT * FROM plans ORDER BY deleted_at IS NOT NULL, position, created_at, id"
    ):
        if row["deleted_at"] is not None:  # its groups and rules went with it
            plans.append(_record(row, ("id",)))
            continue
        groups = [
            _record(
                group,
                ("id", "kind", "type", "name", "position"),
                conditions=json.loads(group["conditions"]),
                options=json.loads(group["options"]),
                hidden=bool(group["hidden"]),
            )
            for group in connection.execute(
                "SELECT * FROM plan_groups WHERE plan_id = ? ORDER BY position, id", (row["id"],)
            )
        ]
        rules = [
            _record(
                rule,
                ("id", "position", "kind", "target"),
                content=json.loads(rule["content"]),
                enabled=bool(rule["enabled"]),
            )
            for rule in connection.execute(
                "SELECT * FROM plan_rules WHERE plan_id = ? ORDER BY position, id", (row["id"],)
            )
        ]
        plan = _record(
            row,
            ("id", "name", "fallback", "position", "created_at"),
            egress=json.loads(row["egress"]),
        )
        plans.append({**plan, "groups": groups, "rules": rules})
    return plans


# -- reading a snapshot: every record checked before anything is written ------------------------
Check = Callable[[Any], bool]


def _text(value: Any) -> bool:
    return isinstance(value, str)


def _integer(value: Any) -> bool:
    return type(value) is int


def _flag(value: Any) -> bool:
    return type(value) is bool


def _time(value: Any) -> bool:
    return type(value) in (int, float) and value >= 0


def _any(_: Any) -> bool:
    return True


def _one_of(*choices: Any) -> Check:
    return lambda value: value in choices


def _optional(check: Check) -> Check:
    return lambda value: value is None or check(value)


_PROFILE: dict[str, Check] = {
    "id": _text,
    "name": _text,
    "kind": _one_of(KIND_SUBSCRIPTION, KIND_FILE),
    "url": _text,
    "path": _text,
    "auto_update_hours": _integer,
    "in_pool": _flag,
    "position": _integer,
    "created_at": _time,
    "updated_at": _time,
}
_CONFIG: dict[str, Check] = {"yaml": _text, "updated_at": _time}
_OVERRIDE: dict[str, Check] = {"scope": _text, "yaml": _text, "updated_at": _time}
_ROUTING: dict[str, Check] = {
    "mode": _optional(_one_of(*MODES)),
    "profile_id": _optional(_text),
    "plan_id": _optional(_text),
    "info_pattern": _optional(_text),
    "updated_at": _time,
}
_PLAN: dict[str, Check] = {
    "id": _text,
    "name": _text,
    "fallback": _text,
    "egress": _any,
    "position": _integer,
    "created_at": _time,
    "updated_at": _time,
}
_GROUP: dict[str, Check] = {
    "id": _text,
    "kind": _one_of(*GROUP_KINDS),
    "type": _text,
    "name": _text,
    "conditions": _any,
    "options": _any,
    "hidden": _flag,
    "position": _integer,
    "updated_at": _time,
}
_RULE: dict[str, Check] = {
    "id": _text,
    "position": _integer,
    "enabled": _flag,
    "kind": _one_of(*RULE_KINDS),
    "content": _any,
    "target": _text,
    "updated_at": _time,
}
_SETTING: dict[str, Check] = {"key": _text, "value": _any, "updated_at": _time}
_HEADER_FIELDS: dict[str, Check] = {"created_at": _time, "device": _text, "app_version": _text}
_SYNCED_SETTINGS = frozenset(f.name for f in fields(Settings) if scope(f.name) == SYNCED)


def _deleted(record: Mapping[str, Any]) -> bool:
    return record.get("deleted_at") is not None


def _check(record: Any, checks: Mapping[str, Check], what: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise BackupError("not_a_backup", f"{what} is not an object")
    for name, check in checks.items():
        if name not in record or not check(record[name]):
            raise BackupError("not_a_backup", f"{what}: {name} {record.get(name)!r}")
    return record


def _records(
    items: Any, checks: Mapping[str, Check], what: str, key: str = "id"
) -> Iterable[dict[str, Any]]:
    """The records of a list, each checked: a tombstone for its key and times only."""
    if not isinstance(items, list):
        raise BackupError("not_a_backup", f"{what} is not a list")
    keys: set[Any] = set()
    for record in items:
        tombstone = isinstance(record, dict) and record.get("deleted_at") is not None
        if tombstone:
            _check(record, {key: checks[key], "updated_at": _time, "deleted_at": _time}, what)
        else:
            _check(record, checks, what)
        if record[key] in keys:
            raise BackupError("not_a_backup", f"{what}: {key} {record[key]!r} twice")
        keys.add(record[key])
        yield record


def _check_document(document: dict[str, Any]) -> None:
    _check(document, _HEADER_FIELDS, "the header")
    for profile in _records(document.get("profiles"), _PROFILE, "profiles"):
        if "config" in profile and not _deleted(profile):
            _check(profile["config"], _CONFIG, "a profile's config")
    for _ in _records(document.get("overrides"), _OVERRIDE, "overrides", key="scope"):
        pass
    routing = _check(document.get("routing"), _ROUTING, "routing")
    for plan in _records(routing.get("plans"), _PLAN, "plans"):
        if not _deleted(plan):
            for _ in _records(plan.get("groups"), _GROUP, "groups"):
                pass
            for _ in _records(plan.get("rules"), _RULE, "rules"):
                pass
    for _ in _records(document.get("settings"), _SETTING, "settings", key="key"):
        pass


# -- restore -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Table:
    name: str
    key: tuple[str, ...]
    columns: tuple[str, ...]  # what a record holds besides its key, as stored
    created_at: bool = False  # set when the row is inserted, kept afterwards


_PROFILES = _Table(
    "profiles",
    ("id",),
    ("name", "kind", "url", "path", "auto_update_hours", "in_pool", "position"),
    created_at=True,
)
_OVERRIDES = _Table("overrides", ("scope",), ("yaml",))
_PLANS = _Table("plans", ("id",), ("name", "fallback", "egress", "position"), created_at=True)
_GROUPS = _Table(
    "plan_groups",
    ("plan_id", "id"),
    ("kind", "type", "name", "conditions", "options", "hidden", "position"),
)
_RULES = _Table(
    "plan_rules", ("id",), ("plan_id", "position", "enabled", "kind", "content", "target")
)


def _canonical(value: Any) -> str:
    # Also the spelling routing.py stores JSON in, so a restored group or rule compares
    # equal to the one the routing page would write.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def restore(connection: sqlite3.Connection, snapshot: Snapshot, mode: str, now: float) -> None:
    """Write the snapshot's data into the database with ``mode``: REPLACE or MERGE."""
    if mode not in RESTORE_MODES:
        raise ValueError(f"invalid restore mode: {mode!r}")
    document = snapshot.document
    profiles = [_stored_profile(profile) for profile in document["profiles"]]
    _restore_rows(connection, _PROFILES, profiles, mode, now)
    _restore_configs(connection, document["profiles"], mode, now)
    _restore_rows(connection, _OVERRIDES, document["overrides"], mode, now)
    routing = document["routing"]
    plans = [_stored_plan(plan) for plan in routing["plans"]]
    _restore_rows(connection, _PLANS, plans, mode, now)
    for plan in routing["plans"]:
        if _deleted(plan):
            continue
        scoped = ("plan_id", plan["id"])
        groups = [_stored_group(plan["id"], group) for group in plan["groups"]]
        _restore_rows(connection, _GROUPS, groups, mode, now, scope=scoped)
        rules = [_stored_rule(plan["id"], rule) for rule in plan["rules"]]
        _restore_rows(connection, _RULES, rules, mode, now, scope=scoped)
    _restore_routing_state(connection, routing, mode, now)
    _restore_settings(connection, document["settings"], mode, now)
    # What was downloaded for a profile no longer there is a cache of nothing.
    connection.execute(
        "DELETE FROM profile_contents WHERE profile_id NOT IN "
        "(SELECT id FROM profiles WHERE deleted_at IS NULL)"
    )


def _stored_profile(record: dict[str, Any]) -> dict[str, Any]:
    if _deleted(record):
        return record
    return {**record, "in_pool": int(record["in_pool"])}


def _stored_plan(record: dict[str, Any]) -> dict[str, Any]:
    if _deleted(record):
        return record
    return {**record, "egress": _canonical(record["egress"])}


def _stored_group(plan_id: str, record: dict[str, Any]) -> dict[str, Any]:
    if _deleted(record):
        return {**record, "plan_id": plan_id}
    converted = {
        "conditions": _canonical(record["conditions"]),
        "options": _canonical(record["options"]),
        "hidden": int(record["hidden"]),
    }
    return {**record, **converted, "plan_id": plan_id}


def _stored_rule(plan_id: str, record: dict[str, Any]) -> dict[str, Any]:
    if _deleted(record):
        return record
    converted = {"content": _canonical(record["content"]), "enabled": int(record["enabled"])}
    return {**record, **converted, "plan_id": plan_id}


def _restore_rows(
    connection: sqlite3.Connection,
    table: _Table,
    records: Sequence[Mapping[str, Any]],
    mode: str,
    now: float,
    *,
    scope: tuple[str, str] | None = None,
) -> None:
    """Merge or replace the rows of ``table`` with ``records``; ``scope`` limits the rows a
    replace marks deleted to those with that column value, such as one plan's groups. A
    record finds its row by key wherever it is, so a rule may move to another plan.

    A merge writes a record that is new here or changed later than the row, with its own
    times; a tombstone marks the row deleted, and is dropped when there is no row. A replace
    inserts the records new here with their times, and stamps ``now`` on the rows it
    changes or marks deleted."""
    key_match = " AND ".join(f"{column} = ?" for column in table.key)
    stored = {
        tuple(row[column] for column in table.key): row
        for row in connection.execute(f"SELECT * FROM {table.name}")
    }
    live: set[tuple[Any, ...]] = set()
    for record in records:
        key = tuple(record[column] for column in table.key)
        row = stored.get(key)
        if mode == MERGE:
            if row is not None and row["updated_at"] >= record["updated_at"]:
                continue
            if _deleted(record):
                if row is not None:
                    connection.execute(
                        f"UPDATE {table.name} SET deleted_at = ?, updated_at = ? WHERE {key_match}",
                        (record["deleted_at"], record["updated_at"], *key),
                    )
                continue
            _write_row(connection, table, record, record["updated_at"], exists=row is not None)
            continue
        if _deleted(record):
            continue  # marked deleted below, if it is here
        live.add(key)
        if row is None:
            _write_row(connection, table, record, record["updated_at"], exists=False)
        elif row["deleted_at"] is not None or any(
            row[column] != record[column] for column in table.columns
        ):
            _write_row(connection, table, record, now, exists=True)
    if mode == REPLACE:
        for key, row in stored.items():
            in_scope = scope is None or row[scope[0]] == scope[1]
            if in_scope and key not in live and row["deleted_at"] is None:
                connection.execute(
                    f"UPDATE {table.name} SET deleted_at = ?, updated_at = ? WHERE {key_match}",
                    (now, now, *key),
                )


def _write_row(
    connection: sqlite3.Connection,
    table: _Table,
    record: Mapping[str, Any],
    updated_at: float,
    *,
    exists: bool,
) -> None:
    if exists:
        assigned = ", ".join(f"{column} = ?" for column in table.columns)
        key_match = " AND ".join(f"{column} = ?" for column in table.key)
        connection.execute(
            f"UPDATE {table.name} SET {assigned}, updated_at = ?, deleted_at = NULL "
            f"WHERE {key_match}",
            (
                *(record[c] for c in table.columns),
                updated_at,
                *(record[c] for c in table.key),
            ),
        )
        return
    columns = (*table.key, *table.columns, *(("created_at",) if table.created_at else ()))
    inserted = ", ".join((*columns, "updated_at"))
    placeholders = ", ".join("?" * (len(columns) + 1))
    connection.execute(
        f"INSERT INTO {table.name} ({inserted}) VALUES ({placeholders})",
        (*(record[c] for c in columns), updated_at),
    )


def _restore_configs(
    connection: sqlite3.Connection, profiles: Sequence[Mapping[str, Any]], mode: str, now: float
) -> None:
    """The configs of the local files, as their own records: a merge keeps the one read
    later. A config the database has but the snapshot lacks stays until its profile goes."""
    live_files = {
        row[0]
        for row in connection.execute(
            "SELECT id FROM profiles WHERE kind = ? AND deleted_at IS NULL", (KIND_FILE,)
        )
    }
    for profile in profiles:
        config = profile.get("config")
        if config is None or _deleted(profile) or profile["id"] not in live_files:
            continue
        row = connection.execute(
            "SELECT yaml, updated_at FROM profile_contents WHERE profile_id = ?", (profile["id"],)
        ).fetchone()
        if row is None:
            updated_at = config["updated_at"]
        elif mode == MERGE:
            if row["updated_at"] >= config["updated_at"]:
                continue
            updated_at = config["updated_at"]
        elif row["yaml"] == config["yaml"]:
            continue
        else:
            updated_at = now
        connection.execute(
            "INSERT INTO profile_contents (profile_id, yaml, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (profile_id) DO UPDATE SET yaml = excluded.yaml, "
            "updated_at = excluded.updated_at",
            (profile["id"], config["yaml"], updated_at),
        )


def _restore_routing_state(
    connection: sqlite3.Connection, routing: Mapping[str, Any], mode: str, now: float
) -> None:
    columns = ("mode", "profile_id", "plan_id", "info_pattern")
    selected = ", ".join(columns)
    row = connection.execute(f"SELECT {selected}, updated_at FROM routing_state").fetchone()
    if mode == MERGE:
        if row["updated_at"] >= routing["updated_at"]:
            return
        updated_at = routing["updated_at"]
    elif all(row[column] == routing[column] for column in columns):
        return
    else:
        # A database that never chose its routing takes the snapshot's as it was.
        updated_at = routing["updated_at"] if row["updated_at"] == 0 else now
    assigned = ", ".join(f"{column} = ?" for column in columns)
    connection.execute(
        f"UPDATE routing_state SET {assigned}, updated_at = ?",
        (*(routing[column] for column in columns), updated_at),
    )


def _restore_settings(
    connection: sqlite3.Connection, records: Sequence[Mapping[str, Any]], mode: str, now: float
) -> None:
    """The synced settings: a merge takes the ones changed later; a replace sets every one
    to the snapshot's value, or to its default when the snapshot has none. A value this
    version does not accept counts as none."""
    incoming = {
        r["key"]: r
        for r in records
        if r["key"] in _SYNCED_SETTINGS and accepted({r["key"]: r["value"]})
    }
    stored = {
        key: (value, updated_at)
        for key, value, updated_at in connection.execute(
            "SELECT key, value, updated_at FROM settings"
        )
    }
    defaults = Settings()
    for name in sorted(_SYNCED_SETTINGS):
        record = incoming.get(name)
        old = stored.get(name)
        if mode == MERGE:
            if record is None or (old is not None and old[1] >= record["updated_at"]):
                continue
            value, updated_at = record["value"], record["updated_at"]
        else:
            value = record["value"] if record else getattr(defaults, name)
            if old is None:
                if record is None:
                    continue  # the default already
                updated_at = record["updated_at"]
            else:
                with contextlib.suppress(ValueError):
                    if json.loads(old[0]) == value:
                        continue
                updated_at = now
        connection.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (name, json.dumps(value), updated_at),
        )
