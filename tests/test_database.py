import gc
import sqlite3
import threading
import warnings
from contextlib import closing
from pathlib import Path

import pytest

from solaris.models.database import (
    DATABASE_NAME,
    MIGRATIONS,
    SCHEMA_VERSION,
    Database,
    Migration,
    read_state,
    sync_rows,
    write_state,
)

TABLES = {
    "profiles",
    "profile_contents",
    "overrides",
    "settings",
    "state",
    "backup_targets",
    "routing_state",
    "plans",
    "plan_groups",
    "plan_rules",
}


def schema(path: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()


def version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("PRAGMA user_version").fetchone()[0]


def test_a_new_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / DATABASE_NAME)
    assert database.version == SCHEMA_VERSION
    with database.reading() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert names >= TABLES
        # The single row of the routing state is there from the start.
        assert tuple(connection.execute("SELECT * FROM routing_state").fetchone()) == (
            1,
            None,
            None,
            None,
            None,
            0.0,
        )
    database.close()


@pytest.mark.parametrize("start", range(SCHEMA_VERSION + 1))
def test_every_version_migrates_to_the_current_schema(tmp_path: Path, start: int) -> None:
    """A database left at any earlier version ends up as a new one does."""
    Database(tmp_path / "new.db").close()
    old = tmp_path / "old.db"
    Database(old, migrations=MIGRATIONS[:start]).close()
    assert version(old) == start
    Database(old).close()
    assert version(old) == SCHEMA_VERSION
    assert schema(old) == schema(tmp_path / "new.db")


def steps(log: list[int], count: int, failing: int | None = None) -> list[Migration]:
    def step(number: int) -> Migration:
        def migrate(connection: sqlite3.Connection) -> None:
            connection.execute(f"CREATE TABLE t{number} (x INTEGER)")
            if number == failing:
                raise sqlite3.OperationalError("disk I/O error")
            log.append(number)

        return migrate

    return [step(number) for number in range(1, count + 1)]


@pytest.mark.parametrize("start", [0, 1, 2])
def test_migrations_run_in_order_from_the_stored_version(tmp_path: Path, start: int) -> None:
    path = tmp_path / DATABASE_NAME
    log: list[int] = []
    Database(path, migrations=steps(log, start)).close()
    log.clear()
    Database(path, migrations=steps(log, 4)).close()
    assert log == list(range(start + 1, 5))
    assert version(path) == 4
    Database(path, migrations=steps(log, 4)).close()  # nothing left to do
    assert log == list(range(start + 1, 5))


def test_a_failing_migration_keeps_the_version_before_it(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_NAME
    log: list[int] = []
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        Database(path, migrations=steps(log, 3, failing=3))
    assert log == [1, 2]
    assert version(path) == 2
    assert "t3" not in {name for name, _ in schema(path)}  # the step was rolled back
    Database(path, migrations=steps(log, 3)).close()  # the next start tries again
    assert log == [1, 2, 3]
    assert version(path) == 3


def test_a_newer_database_is_set_aside(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_NAME
    Database(path, migrations=steps([], 2), clock=lambda: 1_800_000_000).close()
    database = Database(path, migrations=steps([], 1), clock=lambda: 1_800_000_000)
    assert database.version == 1
    database.close()
    aside = tmp_path / f"{DATABASE_NAME}.1800000000.bad"
    assert version(aside) == 2  # kept as it was


def test_a_file_that_is_no_database_is_set_aside(tmp_path: Path) -> None:
    path = tmp_path / DATABASE_NAME
    path.write_bytes(b"not a database, but somebody's data" * 200)
    database = Database(path, clock=lambda: 1_800_000_000)
    assert database.version == SCHEMA_VERSION
    database.close()
    aside = tmp_path / f"{DATABASE_NAME}.1800000000.bad"
    assert aside.read_bytes().startswith(b"not a database")


def test_transactions_commit_or_roll_back(tmp_path: Path) -> None:
    database = Database(tmp_path / DATABASE_NAME)
    with database.transaction() as connection:
        write_state(connection, "kept", {"a": [1, "二"]}, 1.0)

    def fail_midway() -> None:
        with database.transaction() as connection:
            write_state(connection, "lost", 1, 2.0)
            raise RuntimeError

    with pytest.raises(RuntimeError):
        fail_midway()
    with database.reading() as connection:
        assert read_state(connection, "kept") == {"a": [1, "二"]}
        assert read_state(connection, "lost") is None
    database.close()


def test_a_transaction_is_not_seen_half_done(tmp_path: Path) -> None:
    """Another thread waits for a transaction to end instead of reading the middle of it."""
    database = Database(tmp_path / DATABASE_NAME)
    inside, seen = threading.Event(), []

    def read() -> None:
        inside.wait()
        with database.reading() as connection:
            seen.append((read_state(connection, "a"), read_state(connection, "b")))

    reader = threading.Thread(target=read)
    reader.start()
    with database.transaction() as connection:
        write_state(connection, "a", 1, 1.0)
        inside.set()
        reader.join(0.2)
        assert reader.is_alive()  # waiting for the lock
        write_state(connection, "b", 2, 1.0)
    reader.join()
    assert seen == [(1, 2)]
    database.close()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO profiles (id, name, kind, in_pool, position, created_at, updated_at) "
        "VALUES ('a', 'A', 'subscription', 2, 0, 0, 0)",
        "INSERT INTO plan_groups (plan_id, id, kind, type, hidden, position, updated_at) "
        "VALUES ('p', 'g', 'custom', 'select', -1, 0, 0)",
        "INSERT INTO plan_rules (id, plan_id, position, enabled, kind, content, target, "
        "updated_at) VALUES ('r', 'p', 0, 2, 'ip', '{}', 'DIRECT', 0)",
    ],
)
def test_flags_are_zero_or_one(tmp_path: Path, statement: str) -> None:
    database = Database(tmp_path / DATABASE_NAME)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO plans (id, name, fallback, position, created_at, updated_at) "
            "VALUES ('p', 'P', 'DIRECT', 0, 0, 0)"
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"), database.transaction() as c:
        c.execute(statement)
    database.close()


def test_sync_rows_touches_only_what_changed(tmp_path: Path) -> None:
    database = Database(tmp_path / DATABASE_NAME)
    columns = ("id", "name", "fallback", "position")

    def sync(rows: list[tuple[object, ...]], now: float) -> None:
        with database.transaction() as connection:
            sync_rows(connection, "plans", columns, rows, now, created_at=True)

    def stored() -> dict[str, tuple[object, ...]]:
        with database.reading() as connection:
            rows = connection.execute(
                "SELECT id, name, position, created_at, updated_at, deleted_at FROM plans"
            )
            return {row[0]: tuple(row)[1:] for row in rows}

    sync([("a", "A", "DIRECT", 0), ("b", "B", "DIRECT", 1)], 1.0)
    sync([("a", "A", "DIRECT", 0), ("b", "B 2", "DIRECT", 1)], 2.0)
    assert stored() == {"a": ("A", 0, 1.0, 1.0, None), "b": ("B 2", 1, 1.0, 2.0, None)}
    sync([("b", "B 2", "DIRECT", 0)], 3.0)  # a is deleted, b moves up
    assert stored() == {"a": ("A", 0, 1.0, 3.0, 3.0), "b": ("B 2", 0, 1.0, 3.0, None)}
    sync([("b", "B 2", "DIRECT", 0), ("a", "A", "DIRECT", 1)], 4.0)  # a is back
    assert stored()["a"] == ("A", 1, 1.0, 4.0, None)
    database.close()


def test_closing(tmp_path: Path) -> None:
    database = Database(tmp_path / DATABASE_NAME)
    database.close()
    database.close()  # twice is fine
    with pytest.raises(sqlite3.ProgrammingError), database.reading() as connection:
        connection.execute("SELECT 1")


def test_a_database_nobody_closed_closes_quietly(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Database(tmp_path / DATABASE_NAME)
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]
