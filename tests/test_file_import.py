"""The one-time import of the M2 file store (docs/ui.md §2)."""

import json
import shutil
from pathlib import Path

import pytest

from mihomo_desktop.models.database import Database, read_state
from mihomo_desktop.models.file_import import INDEX_NAME
from mihomo_desktop.models.profiles import KIND_FILE, KIND_SUBSCRIPTION, Profile, ProfileList, Usage
from mihomo_desktop.models.settings import DatabaseSettingsStore, Settings
from mihomo_desktop.models.store import FILE_IMPORT_KEY, ProfileStore, open_database
from tests.support.storage import allow_writes, refuse_writes, stored

SAMPLE = Path(__file__).resolve().parent / "data" / "m2-store"
A = "3f9c2b7e8d4a4c1f9e6b5a2d1c0f8e7a"
B = "8a1d4e6f2b3c4d5e9f0a1b2c3d4e5f60"
ORPHAN = "0000000000000000000000000000dead"
SAMPLE_FILES = [
    "override.yaml",
    f"overrides/{ORPHAN}.yaml",
    f"overrides/{B}.yaml",
    "profiles.json",
    f"profiles/{A}.yaml",
    f"profiles/{B}.yaml",
    "settings.json",
]
IMPORTED = ProfileList(
    (
        Profile(
            id=A,
            name="示例订阅 A",
            kind=KIND_SUBSCRIPTION,
            url="https://sub.example.com/api/v1/client/subscribe?token=sample",
            auto_update_hours=12,
            updated_at=1793277795.0,
            usage=Usage(1288490188, 39728447488, 214748364800, 1798646400),
            proxy_count=2,
        ),
        Profile(
            id=B,
            name="本地配置",
            kind=KIND_FILE,
            path="/Users/sample/config.yaml",
            updated_at=1792500000.0,
            has_override=True,
        ),
    ),
    A,
    has_global_override=True,
)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """A copy of a data directory the M2 file store wrote."""
    directory = tmp_path / "data"
    shutil.copytree(SAMPLE, directory)
    return directory


def files(directory: Path) -> list[str]:
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and not path.name.startswith("mihomo-desktop.db")
    )


def migrated(names: list[str]) -> list[str]:
    return sorted(f"{name}.migrated" for name in names)


def marker(directory: Path) -> object:
    database = Database(directory / "mihomo-desktop.db")
    try:
        with database.reading() as connection:
            return read_state(connection, FILE_IMPORT_KEY)
    finally:
        database.close()


def test_the_sample_store_is_imported(data: Path) -> None:
    store = ProfileStore(data)
    assert store.load() == IMPORTED
    assert store.read_config(A) == (SAMPLE / "profiles" / f"{A}.yaml").read_text("utf-8")
    assert store.read_config(B).startswith("mixed-port: 7890\n")
    assert store.read_override(None) == "mode: rule\nlog-level: warning\n"
    assert store.read_override(B) == "prepend-rules:\n  - DOMAIN-SUFFIX,example.org,DIRECT\n"
    # Only the settings that differ from the defaults are stored, with their scopes.
    settings = DatabaseSettingsStore(store.database)
    assert settings.current == Settings(language="en", close_action="quit", mixed_port=7897)
    assert sorted(stored(data, "SELECT key, scope FROM settings")) == [
        ("close_action", "local"),
        ("language", "synced"),
        ("mixed_port", "local"),
    ]
    # The override of a profile the index does not list is not imported, but kept.
    assert stored(data, "SELECT scope FROM overrides ORDER BY scope") == [(B,), ("global",)]
    # Every file is kept under a new name; nothing is deleted.
    assert files(data) == migrated(SAMPLE_FILES)
    assert (data / f"overrides/{ORPHAN}.yaml.migrated").read_text("utf-8") == "mode: direct\n"
    store.close()
    assert marker(data) == {"pending": []}


def test_the_import_happens_once(data: Path) -> None:
    ProfileStore(data).close()
    # An older version started meanwhile and wrote a file store of its own.
    shutil.copy(SAMPLE / INDEX_NAME, data / INDEX_NAME)
    store = ProfileStore(data)
    store.save(ProfileList())
    store.close()
    assert ProfileStore(data).load() == ProfileList(has_global_override=True)
    assert (data / INDEX_NAME).exists()  # not ours to rename


def test_a_damaged_index_imports_no_profiles(data: Path) -> None:
    """As the file store would have started with an empty list; the rest is imported."""
    (data / INDEX_NAME).write_text('{"version": 1, "profiles": [', encoding="utf-8")
    store = ProfileStore(data)
    assert store.load() == ProfileList(has_global_override=True)
    assert stored(data, "SELECT scope FROM overrides") == [("global",)]
    assert DatabaseSettingsStore(store.database).current.language == "en"
    assert files(data) == migrated(SAMPLE_FILES)
    assert (data / "profiles.json.migrated").read_text("utf-8").endswith("[")


@pytest.mark.parametrize(
    "index",
    [
        {"version": 2, "profiles": []},
        {"version": 1, "profiles": [{"name": "no id"}]},
        {"version": 1, "profiles": "none"},
        ["not", "an", "object"],
    ],
)
def test_an_index_of_another_format_imports_no_profiles(data: Path, index: object) -> None:
    (data / INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    assert ProfileStore(data).load().profiles == ()
    assert files(data) == migrated(SAMPLE_FILES)


def test_a_profile_whose_config_is_missing(data: Path) -> None:
    """It is imported as the file store showed it, and asks to be imported again on use."""
    (data / "profiles" / f"{B}.yaml").unlink()
    store = ProfileStore(data)
    assert [p.id for p in store.load().profiles] == [A, B]
    with pytest.raises(Exception, match="config_missing"):
        store.read_config(B)


def test_a_failure_midway_leaves_nothing_and_is_retried(data: Path) -> None:
    Database(data / "mihomo-desktop.db").close()
    refuse_writes(data, "overrides")  # profiles and configs are written before overrides
    store = ProfileStore(data)
    assert store.load() == ProfileList()
    assert stored(data, "SELECT count(*) FROM profiles") == [(0,)]
    assert stored(data, "SELECT count(*) FROM profile_contents") == [(0,)]
    assert stored(data, "SELECT count(*) FROM settings") == [(0,)]
    assert files(data) == SAMPLE_FILES  # nothing renamed
    store.close()
    assert marker(data) is None

    allow_writes(data, "overrides")
    store = ProfileStore(data)  # the next start
    assert store.load() == IMPORTED
    assert files(data) == migrated(SAMPLE_FILES)


def test_a_file_that_cannot_be_read_stops_the_import(data: Path) -> None:
    config = data / "profiles" / f"{A}.yaml"
    text = config.read_text("utf-8")
    config.unlink()
    config.mkdir()  # reading it fails, and not because it is missing
    assert ProfileStore(data).load() == ProfileList()
    assert (data / INDEX_NAME).exists()

    config.rmdir()
    config.write_text(text, encoding="utf-8")
    assert ProfileStore(data).load() == IMPORTED


def test_renaming_is_retried(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rename = Path.rename

    def locked(path: Path, target: Path) -> Path:
        if path.name == "settings.json":
            raise PermissionError("the file is in use")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", locked)
    store = ProfileStore(data)
    assert store.load() == IMPORTED
    store.close()
    assert (data / "settings.json").exists()
    assert marker(data) == {"pending": ["settings.json"]}

    monkeypatch.undo()
    store = ProfileStore(data)  # imports nothing again, only renames
    assert store.load() == IMPORTED
    store.close()
    assert files(data) == migrated(SAMPLE_FILES)
    assert marker(data) == {"pending": []}


def test_a_name_already_taken_gets_a_number(data: Path) -> None:
    (data / "settings.json.migrated").write_text("{}", encoding="utf-8")
    ProfileStore(data).close()
    assert (data / "settings.json.migrated").read_text("utf-8") == "{}"
    assert json.loads((data / "settings.json.migrated.2").read_text("utf-8"))["language"] == "en"


def test_what_was_stored_after_a_failed_import_stays(data: Path) -> None:
    """The import failed, the user went on with an empty list, then it succeeded."""
    database = Database(data / "mihomo-desktop.db")
    database.close()
    refuse_writes(data, "overrides")
    store = ProfileStore(data)
    allow_writes(data, "overrides")
    mine = Profile(id="mine", name="新的", kind=KIND_SUBSCRIPTION, url="https://e/x")
    store.write_config("mine", "proxies: []\n")
    store.save(ProfileList((mine,), "mine"))
    store.write_override(None, "mode: global\n")
    DatabaseSettingsStore(store.database).update(language="zh")
    store.close()

    store = ProfileStore(data)
    loaded = store.load()
    assert [p.id for p in loaded.profiles] == ["mine", A, B]
    assert loaded.active == "mine"
    assert store.read_override(None) == "mode: global\n"
    settings = DatabaseSettingsStore(store.database).current
    assert (settings.language, settings.mixed_port) == ("zh", 7897)


def test_settings_alone_are_imported(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text('{"appearance": "dark"}', encoding="utf-8")
    database = open_database(tmp_path)
    assert DatabaseSettingsStore(database).current == Settings(appearance="dark")
    assert (tmp_path / "settings.json.migrated").exists()


def test_a_new_data_directory_has_nothing_to_import(tmp_path: Path) -> None:
    ProfileStore(tmp_path).close()
    assert marker(tmp_path) == {"pending": []}
