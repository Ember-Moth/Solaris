import json
import sys
from pathlib import Path

import pytest

from mihomo_desktop.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
)
from mihomo_desktop.models.store import INDEX_NAME, ProfileStore, default_data_dir, write_atomic

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
LOCAL = Profile(id="b2", name="config", kind=KIND_FILE, path="/tmp/config.yaml")


def test_profiles_round_trip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    assert store.load() == ProfileList()
    store.write_config("a1", "proxies: []\n")
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1", frozenset({"a1"})))

    loaded = ProfileStore(tmp_path).load()
    # Update failures are not stored.
    assert loaded == ProfileList((SUBSCRIPTION, LOCAL), "a1")
    assert store.read_config("a1") == "proxies: []\n"


def test_index_format(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.save(ProfileList((SUBSCRIPTION,), "a1"))
    document = json.loads((tmp_path / INDEX_NAME).read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert document["active"] == "a1"
    (entry,) = document["profiles"]
    assert entry["name"] == "示例订阅 A"
    assert entry["usage"] == {"upload": 1, "download": 2, "total": 300, "expire": 1_800_000_000}
    assert "override" not in entry  # an override is a file of its own
    assert "has_override" not in entry
    assert store.config_path("a1") == tmp_path / "profiles" / "a1.yaml"


def test_overrides_are_files_beside_the_index(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    store.save(ProfileList((SUBSCRIPTION, LOCAL), "a1"))
    assert store.read_override(None) == ""
    store.write_override(None, "mode: global\n")
    store.write_override("b2", "prepend-rules: [MATCH,DIRECT]\n")
    assert (tmp_path / "override.yaml").read_text(encoding="utf-8") == "mode: global\n"
    assert store.override_path("b2") == tmp_path / "overrides" / "b2.yaml"

    loaded = ProfileStore(tmp_path).load()
    assert loaded.has_global_override
    assert [p.has_override for p in loaded.profiles] == [False, True]
    assert store.read_override("b2") == "prepend-rules: [MATCH,DIRECT]\n"

    # Blank text removes an override; removing a profile's config removes its override.
    store.write_override(None, "\n  \n")
    store.delete_config("b2")
    assert not store.override_path(None).exists()
    assert not store.override_path("b2").exists()
    loaded = store.load()
    assert not loaded.has_global_override
    assert not loaded.get("b2").has_override
    assert [p.name for p in (tmp_path / "overrides").iterdir()] == []


def test_reads_the_first_release_index(tmp_path: Path) -> None:
    """The index the profiles page shipped with reserved ``"override": null``."""
    document = {
        "version": 1,
        "active": "a1",
        "profiles": [
            {
                "id": "a1",
                "name": "示例订阅 A",
                "kind": "subscription",
                "url": "https://sub.example.com/s?token=x",
                "path": "",
                "auto_update_hours": 12,
                "updated_at": 1_790_000_000.0,
                "usage": {"upload": 1, "download": 2, "total": 300, "expire": 1_800_000_000},
                "proxy_count": 48,
                "override": None,
            }
        ],
    }
    (tmp_path / INDEX_NAME).write_text(json.dumps(document), encoding="utf-8")
    store = ProfileStore(tmp_path)
    loaded = store.load()
    assert loaded == ProfileList((SUBSCRIPTION,), "a1")
    store.save(loaded)
    (entry,) = json.loads((tmp_path / INDEX_NAME).read_text(encoding="utf-8"))["profiles"]
    assert "override" not in entry


def test_writes_leave_no_temporary_files(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.json"
    write_atomic(target, b"one")
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["file.json"]


def test_unreadable_index_is_set_aside(tmp_path: Path) -> None:
    (tmp_path / INDEX_NAME).write_text("{not json", encoding="utf-8")
    store = ProfileStore(tmp_path)
    assert store.load() == ProfileList()
    assert not (tmp_path / INDEX_NAME).exists()
    (aside,) = tmp_path.glob(f"{INDEX_NAME}.*.bad")
    assert aside.read_text(encoding="utf-8") == "{not json"


def test_newer_index_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / INDEX_NAME).write_text('{"version": 2, "profiles": []}', encoding="utf-8")
    assert ProfileStore(tmp_path).load() == ProfileList()
    assert len(list(tmp_path.glob(f"{INDEX_NAME}.*.bad"))) == 1


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


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Application Support/Mihomo Desktop"),
        ("win32", {"LOCALAPPDATA": "{home}/Local"}, "Local/Mihomo Desktop"),
        ("linux", {"XDG_DATA_HOME": ""}, ".local/share/Mihomo Desktop"),
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
