import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from solaris.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
)
from solaris.models.settings import DatabaseSettingsStore
from solaris.models.store import ProfileStore, default_data_dir
from tests.support.storage import refuse_writes, stored

SUBSCRIPTION = Profile(
    id="a1",
    name="示例订阅 A",
    kind=KIND_SUBSCRIPTION,
    url="https://sub.example.com/s?token=x",
    auto_update_hours=12,
    updated_at=1_790_000_000.0,
    usage=Usage(1, 2, 300, 1_800_000_000),
    proxy_count=48,
)
LOCAL = Profile(id="b2", name="config", kind=KIND_FILE, path="/tmp/config.yaml", in_pool=False)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def rows(directory: Path, table: str, columns: str) -> dict[str, tuple[object, ...]]:
    return {row[0]: row[1:] for row in stored(directory, f"SELECT {columns} FROM {table}")}


def test_profiles_round_trip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    assert store.load() == ProfileList()
    store.write_config("a1", "proxies: []\n")
    store.write_config("b2", "mixed-port: 7890\n")
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1", frozenset({"a1"})))
    store.close()

    store = ProfileStore(tmp_path)
    # Update failures are not stored.
    assert store.load() == ProfileList((SUBSCRIPTION, LOCAL), "a1")
    assert store.read_config("a1") == "proxies: []\n"
    assert store.load().get("b2").in_pool is False
    store.save(ProfileList((LOCAL, SUBSCRIPTION), "b2"))  # reordered, another one in use
    assert store.load() == ProfileList((LOCAL, SUBSCRIPTION), "b2")
    # The profile in use is the one the routing state names (routing.md §9).
    assert stored(tmp_path, "SELECT profile_id FROM routing_state") == [("b2",)]


def test_what_came_with_a_config_is_stored_with_it(tmp_path: Path) -> None:
    """The time, usage and proxy count describe the downloaded config, a cache of this
    machine (storage.md §2), so they are kept beside it and not with the profile."""
    store = ProfileStore(tmp_path)
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    assert store.load().get("a1") == replace(
        SUBSCRIPTION, updated_at=0.0, usage=None, proxy_count=None
    )
    store.write_config("a1", "proxies: []\n")
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    assert store.load().get("a1") == SUBSCRIPTION
    columns = "profile_id, fetched_at, upload, download, total, expire, proxy_count, unread"
    assert rows(tmp_path, "profile_contents", columns) == {
        "a1": (1_790_000_000.0, 1, 2, 300, 1_800_000_000, 48, 0)
    }
    # Stored while no core could read it (D27), until one did.
    store.save(ProfileList((replace(SUBSCRIPTION, proxy_count=None, unread=True),), "a1"))
    assert store.load().get("a1").unread


def test_records_change_only_when_their_values_do(tmp_path: Path) -> None:
    clock = Clock()
    store = ProfileStore(tmp_path, clock=clock)
    store.write_config("a1", "proxies: []\n")
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))
    clock.now = 2_000.0
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))  # nothing changed
    store.save(ProfileList((SUBSCRIPTION, replace(LOCAL, name="renamed")), "a1"))
    columns = "id, created_at, updated_at, deleted_at"
    assert rows(tmp_path, "profiles", columns) == {
        "a1": (1_000.0, 1_000.0, None),
        "b2": (1_000.0, 2_000.0, None),
    }
    # An update that only downloaded the config again leaves the profile as it was.
    clock.now = 3_000.0
    store.write_config("a1", "proxies: []\n")
    store.save(ProfileList((replace(SUBSCRIPTION, updated_at=3_000.0), LOCAL), "a1"))
    assert rows(tmp_path, "profiles", columns)["a1"] == (1_000.0, 1_000.0, None)
    assert rows(tmp_path, "profile_contents", "profile_id, updated_at")["a1"] == (3_000.0,)


def test_removed_profiles_are_marked_deleted(tmp_path: Path) -> None:
    clock = Clock()
    store = ProfileStore(tmp_path, clock=clock)
    for profile in (SUBSCRIPTION, LOCAL):
        store.write_config(profile.id, "proxies: []\n")
    store.write_override("b2", "mode: direct\n")
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))

    clock.now = 2_000.0
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    store.delete_config("b2")
    assert store.load() == ProfileList((SUBSCRIPTION,), "a1")
    # The record stays, marked, for a backup to merge; the config, a cache, is gone.
    assert rows(tmp_path, "profiles", "id, updated_at, deleted_at")["b2"] == (2_000.0, 2_000.0)
    assert rows(tmp_path, "overrides", "scope, deleted_at") == {"b2": (2_000.0,)}
    with pytest.raises(ProfileError) as raised:
        store.read_config("b2")
    assert raised.value.code == "config_missing"
    assert store.read_override("b2") == ""

    clock.now = 3_000.0
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))  # the same id is restored
    assert rows(tmp_path, "profiles", "id, updated_at, deleted_at")["b2"] == (3_000.0, None)


def test_overrides(tmp_path: Path) -> None:
    clock = Clock()
    store = ProfileStore(tmp_path, clock=clock)
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))
    assert store.read_override(None) == ""
    store.write_override(None, "mode: global\n")
    store.write_override("b2", "prepend-rules: [MATCH,DIRECT]\n")

    loaded = ProfileStore(tmp_path).load()
    assert loaded.has_global_override
    assert [p.has_override for p in loaded.profiles] == [False, True]
    assert store.read_override(None) == "mode: global\n"
    assert store.read_override("b2") == "prepend-rules: [MATCH,DIRECT]\n"

    # Blank text deletes an override: the record is marked, and writing again restores it.
    clock.now = 2_000.0
    store.write_override(None, "\n  \n")
    loaded = store.load()
    assert not loaded.has_global_override
    assert store.read_override(None) == ""
    assert rows(tmp_path, "overrides", "scope, updated_at, deleted_at")["global"] == (
        2_000.0,
        2_000.0,
    )
    clock.now = 3_000.0
    store.write_override(None, "mode: rule\n")
    assert store.read_override(None) == "mode: rule\n"
    assert rows(tmp_path, "overrides", "scope, updated_at, deleted_at")["global"] == (
        3_000.0,
        None,
    )


def test_active_must_name_a_profile(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.save(ProfileList((LOCAL,), "gone"))
    assert store.load().active is None


def test_missing_config_is_a_profile_error(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    with pytest.raises(ProfileError) as raised:
        store.read_config("nope")
    assert raised.value.code == "config_missing"
    store.delete_config("nope")  # deleting twice is fine


def test_a_config_no_profile_names_goes_at_the_next_start(tmp_path: Path) -> None:
    """A crash between writing a new profile's config and saving the list leaves it behind."""
    store = ProfileStore(tmp_path)
    store.write_config("a1", "proxies: []\n")
    store.write_config("new", "proxies: []\n")
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    store.close()
    ProfileStore(tmp_path).close()
    assert stored(tmp_path, "SELECT profile_id FROM profile_contents") == [("a1",)]


def test_failed_writes_are_profile_errors(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.write_config("a1", "proxies: []\n")
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    refuse_writes(tmp_path, "profiles")
    with pytest.raises(ProfileError) as raised:
        store.save(ProfileList((SUBSCRIPTION, LOCAL), "b2"))
    assert raised.value.code == "storage_failed"
    assert "disk is full" in raised.value.message
    assert store.load() == ProfileList((SUBSCRIPTION,), "a1")  # all or nothing


def test_threads_share_the_store(tmp_path: Path) -> None:
    """The asyncio thread's workers and the Qt thread read and write at the same time."""
    store = ProfileStore(tmp_path)
    settings = DatabaseSettingsStore(store.database)
    profiles = [
        Profile(id=f"p{n}", name=f"配置 {n}", kind=KIND_SUBSCRIPTION, url=f"https://e/{n}")
        for n in range(8)
    ]
    errors: list[BaseException] = []
    lock = threading.Lock()  # the library writes the list under its own lock
    saved = [ProfileList()]

    def write(profile: Profile) -> None:
        try:
            for round_ in range(20):
                store.write_config(profile.id, f"# {round_}\nproxies: []\n")
                store.write_override(profile.id, f"mode: {'rule' if round_ % 2 else 'direct'}\n")
                with lock:
                    saved[0] = saved[0].put(replace(profile, auto_update_hours=round_))
                    store.save(saved[0])
                settings.update(mixed_port=10_000 + round_)
        except BaseException as error:
            errors.append(error)

    def read() -> None:
        try:
            for _ in range(100):
                listed = store.load()
                for profile in listed.profiles:
                    assert store.read_config(profile.id).endswith("proxies: []\n")
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(p,)) for p in profiles]
    threads += [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    loaded = ProfileStore(tmp_path).load()
    assert {p.id: p.auto_update_hours for p in loaded.profiles} == {p.id: 19 for p in profiles}
    assert all(store.read_override(p.id) == "mode: rule\n" for p in profiles)
    assert settings.current.mixed_port == 10_019
    assert DatabaseSettingsStore(store.database).current == settings.current


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Application Support/Solaris"),
        ("win32", {"LOCALAPPDATA": "{home}/Local"}, "Local/Solaris"),
        ("linux", {"XDG_DATA_HOME": ""}, ".local/share/Solaris"),
    ],
)
def test_default_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform: str,
    env: dict[str, str],
    expected: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value.format(home=tmp_path))
    assert default_data_dir() == tmp_path / expected
