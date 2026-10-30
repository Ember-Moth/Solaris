import json
import threading
from dataclasses import fields
from pathlib import Path

import pytest

from solaris.host.base import RunOptions
from solaris.models.database import Database
from solaris.models.settings import (
    LOCAL,
    SYNCED,
    DatabaseSettingsStore,
    Settings,
    SettingsError,
    SettingsStore,
    scope,
    write_settings,
)
from solaris.models.store import open_database
from tests.support.storage import allow_writes, refuse_writes, stored


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return open_database(tmp_path, clock=lambda: 1_000.0)


def rows(directory: Path) -> dict[str, tuple[object, ...]]:
    query = "SELECT key, value, scope, updated_at FROM settings"
    return {row[0]: (json.loads(str(row[1])), *row[2:]) for row in stored(directory, query)}


def test_every_setting_has_a_scope() -> None:
    assert {f.name: scope(f.name) for f in fields(Settings)} == {
        "language": SYNCED,
        "appearance": SYNCED,
        "close_action": LOCAL,
        "launch_at_login": LOCAL,
        "tun": LOCAL,
        "system_proxy": LOCAL,
        "mixed_port": LOCAL,
        "allow_lan": LOCAL,
        "tun_stack": LOCAL,
        "include_all_networks": LOCAL,
        "warp_protocol": LOCAL,
        "update_check": SYNCED,
        "update_check_hours": SYNCED,
    }


def test_defaults_without_rows(tmp_path: Path, database: Database) -> None:
    store = DatabaseSettingsStore(database)
    assert store.current == Settings()
    assert rows(tmp_path) == {}  # nothing is written until a change
    assert store.current.run_options() == RunOptions(mixed_port=7890)


def test_update_writes_and_notifies(tmp_path: Path, database: Database) -> None:
    store: SettingsStore = DatabaseSettingsStore(database)
    seen: list[tuple[Settings, Settings]] = []
    stop = store.subscribe(lambda before, after: seen.append((before, after)))

    store.update(language="en", mixed_port=7897, allow_lan=True, tun=True)
    changed = Settings(language="en", tun=True, mixed_port=7897, allow_lan=True)
    assert store.current == changed
    assert seen == [(Settings(), changed)]
    assert DatabaseSettingsStore(database).current == changed
    assert store.current.run_options() == RunOptions(True, False, 7897, True)
    # One row per setting changed, with the scope it has (storage.md §2).
    assert rows(tmp_path) == {
        "language": ("en", SYNCED, 1_000.0),
        "tun": (True, LOCAL, 1_000.0),
        "mixed_port": (7897, LOCAL, 1_000.0),
        "allow_lan": (True, LOCAL, 1_000.0),
    }

    store.update(language="en")  # no change, no write, nobody told
    assert len(seen) == 1
    stop()
    store.update(language="zh")
    assert len(seen) == 1
    assert rows(tmp_path)["language"] == ("zh", SYNCED, 1_000.0)


def test_run_options_carry_the_stack_and_all_networks(database: Database) -> None:
    store = DatabaseSettingsStore(database)
    assert store.current.run_options().tun_stack == ""  # "profile": the profile's own
    for stack in ("system", "mips", "gvisor", "mixed"):
        assert store.update(tun_stack=stack).run_options().tun_stack == stack
    options = store.update(include_all_networks=True).run_options()
    assert options == RunOptions(mixed_port=7890, tun_stack="mixed", include_all_networks=True)


@pytest.mark.parametrize(
    "change",
    [
        {"language": "fr"},
        {"appearance": "sepia"},
        {"close_action": "minimize"},
        {"mixed_port": 80},
        {"mixed_port": 70000},
        {"mixed_port": True},
        {"update_check_hours": 12},
        {"update_check_hours": True},
        {"allow_lan": "yes"},
        {"tun_stack": ""},
        {"tun_stack": "gVisor"},
        {"include_all_networks": 1},
    ],
)
def test_update_refuses_invalid_values(
    tmp_path: Path, database: Database, change: dict[str, object]
) -> None:
    store = DatabaseSettingsStore(database)
    store.subscribe(lambda before, after: pytest.fail("nothing changed"))
    with pytest.raises(ValueError, match="invalid"):
        store.update(**change)
    assert store.current == Settings()
    assert rows(tmp_path) == {}


def test_failed_write_keeps_the_settings(tmp_path: Path, database: Database) -> None:
    store = DatabaseSettingsStore(database)
    store.subscribe(lambda before, after: pytest.fail("nothing changed"))
    refuse_writes(tmp_path, "settings")
    with pytest.raises(SettingsError) as raised:
        store.update(allow_lan=True, language="en")
    assert raised.value.code == "settings_unwritable"
    assert store.current == Settings()
    allow_writes(tmp_path, "settings")
    assert DatabaseSettingsStore(database).current == Settings()  # neither was written


def test_unacceptable_values_take_defaults(database: Database) -> None:
    with database.transaction() as connection:
        connection.executemany(
            "INSERT INTO settings (key, value, scope, updated_at) VALUES (?, ?, 'synced', 0)",
            [
                ("language", '"zh"'),
                ("appearance", '"neon"'),
                ("mixed_port", '"7890"'),
                ("allow_lan", "1"),
                ("tun", "{not json"),
                ("removed_setting", "true"),
            ],
        )
    assert DatabaseSettingsStore(database).current == Settings(language="zh")


def test_changes_by_other_writers_are_heard_on_reload(database: Database) -> None:
    """A restore writes the table itself; the listeners still hear of the change."""
    store = DatabaseSettingsStore(database)
    seen: list[tuple[Settings, Settings]] = []
    store.subscribe(lambda before, after: seen.append((before, after)))
    with database.transaction() as connection:
        write_settings(connection, {"appearance": "dark", "close_action": "quit"}, 2_000.0)
    assert store.current == Settings()  # not until it reads the table again
    after = Settings(appearance="dark", close_action="quit")
    assert store.reload() == after
    assert seen == [(Settings(), after)]
    store.reload()  # nothing new
    assert len(seen) == 1


def test_the_import_keeps_what_is_stored(database: Database) -> None:
    with database.transaction() as connection:
        write_settings(connection, {"language": "en"}, 2_000.0)
        write_settings(connection, {"language": "zh", "tun": True}, 3_000.0, keep=True)
    assert DatabaseSettingsStore(database).current == Settings(language="en", tun=True)


def test_changes_from_several_threads(database: Database) -> None:
    """Every change is heard once, on the thread that made it, and none is lost."""
    store = DatabaseSettingsStore(database)
    heard: list[tuple[str, int]] = []
    store.subscribe(
        lambda before, after: heard.append((threading.current_thread().name, after.mixed_port))
    )

    def change(offset: int) -> None:
        for port in range(20_000 + offset * 100, 20_000 + offset * 100 + 50):
            store.update(mixed_port=port)

    threads = [threading.Thread(target=change, args=(n,), name=f"t{n}") for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(heard) == 6 * 50
    assert all(port // 100 - 200 == int(name[1:]) for name, port in heard)
    assert DatabaseSettingsStore(database).current == store.current
