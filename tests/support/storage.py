"""Helpers for tests of the local database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from mihomo_desktop.models.database import DATABASE_NAME


def refuse_writes(directory: Path, table: str, message: str = "disk is full") -> None:
    """Make every insert into and update of ``table`` fail, for every connection to the
    database in ``directory``, as a full disk would."""
    with closing(sqlite3.connect(directory / DATABASE_NAME, autocommit=True)) as connection:
        for event in ("INSERT", "UPDATE"):
            connection.execute(
                f"CREATE TRIGGER refuse_{event.lower()}_{table} BEFORE {event} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
            )


def allow_writes(directory: Path, table: str) -> None:
    with closing(sqlite3.connect(directory / DATABASE_NAME, autocommit=True)) as connection:
        for event in ("insert", "update"):
            connection.execute(f"DROP TRIGGER refuse_{event}_{table}")


def stored(directory: Path, query: str, *parameters: object) -> list[tuple[Any, ...]]:
    """The rows of a query, read through a connection of its own."""
    with closing(sqlite3.connect(directory / DATABASE_NAME, autocommit=True)) as connection:
        return connection.execute(query, parameters).fetchall()


def has_config(directory: Path, profile_id: str) -> bool:
    return bool(
        stored(directory, "SELECT 1 FROM profile_contents WHERE profile_id = ?", profile_id)
    )


def has_override(directory: Path, scope: str) -> bool:
    query = "SELECT 1 FROM overrides WHERE scope = ? AND deleted_at IS NULL"
    return bool(stored(directory, query, scope))
