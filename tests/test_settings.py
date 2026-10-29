import json
from dataclasses import fields
from pathlib import Path

import pytest

from mihomo_desktop.host.base import RunOptions
from mihomo_desktop.models.settings import (
    LOCAL,
    SETTINGS_NAME,
    SYNCED,
    JsonSettingsStore,
    Settings,
    SettingsError,
    SettingsStore,
    scope,
)


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
    }


def test_defaults_without_a_file(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path)
    assert store.current == Settings()
    assert not (tmp_path / SETTINGS_NAME).exists()  # nothing is written until a change
    assert store.current.run_options() == RunOptions(mixed_port=7890)


def test_update_writes_and_notifies(tmp_path: Path) -> None:
    store: SettingsStore = JsonSettingsStore(tmp_path)
    seen: list[tuple[Settings, Settings]] = []
    stop = store.subscribe(lambda before, after: seen.append((before, after)))

    store.update(language="en", mixed_port=7897, allow_lan=True, tun=True)
    changed = Settings(language="en", tun=True, mixed_port=7897, allow_lan=True)
    assert store.current == changed
    assert seen == [(Settings(), changed)]
    assert JsonSettingsStore(tmp_path).current == changed
    assert store.current.run_options() == RunOptions(True, False, 7897, True)

    store.update(language="en")  # no change, no write, nobody told
    assert len(seen) == 1
    stop()
    store.update(language="zh")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"language": "fr"},
        {"appearance": "sepia"},
        {"close_action": "minimize"},
        {"mixed_port": 80},
        {"mixed_port": 70000},
        {"mixed_port": True},
        {"allow_lan": "yes"},
    ],
)
def test_update_refuses_invalid_values(tmp_path: Path, change: dict[str, object]) -> None:
    store = JsonSettingsStore(tmp_path)
    store.subscribe(lambda before, after: pytest.fail("nothing changed"))
    with pytest.raises(ValueError, match="invalid"):
        store.update(**change)
    assert store.current == Settings()
    assert not (tmp_path / SETTINGS_NAME).exists()


def test_failed_write_keeps_the_settings(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path)
    store.subscribe(lambda before, after: pytest.fail("nothing changed"))
    (tmp_path / SETTINGS_NAME).mkdir()  # the rename onto it fails
    with pytest.raises(SettingsError) as raised:
        store.update(allow_lan=True)
    assert raised.value.code == "settings_unwritable"
    assert store.current == Settings()


def test_unacceptable_values_take_defaults(tmp_path: Path) -> None:
    document = {"language": "zh", "appearance": "neon", "mixed_port": "7890", "allow_lan": 1}
    (tmp_path / SETTINGS_NAME).write_text(json.dumps(document), encoding="utf-8")
    assert JsonSettingsStore(tmp_path).current == Settings(language="zh")


def test_unreadable_file_is_set_aside(tmp_path: Path) -> None:
    (tmp_path / SETTINGS_NAME).write_text("{not json", encoding="utf-8")
    store = JsonSettingsStore(tmp_path)
    assert store.current == Settings()
    (kept,) = tmp_path.glob(f"{SETTINGS_NAME}.*.bad")
    store.update(language="en")
    assert kept.read_text(encoding="utf-8") == "{not json"
