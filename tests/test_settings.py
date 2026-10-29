import json
from pathlib import Path

import pytest

from mihomo_desktop.host.base import RunOptions
from mihomo_desktop.models.settings import SETTINGS_NAME, Settings, SettingsError, SettingsStore


def test_defaults_without_a_file(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    assert store.current == Settings()
    assert not (tmp_path / SETTINGS_NAME).exists()  # nothing is written until a change
    assert store.current.run_options() == RunOptions(mixed_port=7890)


def test_update_writes_the_file(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.update(language="en", mixed_port=7897, allow_lan=True, tun=True)

    document = json.loads((tmp_path / SETTINGS_NAME).read_text(encoding="utf-8"))
    assert document == {
        "version": 1,
        "language": "en",
        "appearance": "system",
        "close_action": "tray",
        "launch_at_login": False,
        "tun": True,
        "system_proxy": False,
        "mixed_port": 7897,
        "allow_lan": True,
    }
    assert SettingsStore(tmp_path).current == store.current
    assert store.current.run_options() == RunOptions(True, False, 7897, True)


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
    store = SettingsStore(tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        store.update(**change)
    assert store.current == Settings()
    assert not (tmp_path / SETTINGS_NAME).exists()


def test_invalid_and_unknown_keys_take_defaults(tmp_path: Path) -> None:
    document = {
        "version": 1,
        "language": "zh",
        "appearance": "neon",  # not a choice
        "mixed_port": "7890",  # not a number
        "allow_lan": 1,  # not a boolean
        "close_action": "quit",
        "from_a_later_release": True,
    }
    (tmp_path / SETTINGS_NAME).write_text(json.dumps(document), encoding="utf-8")
    assert SettingsStore(tmp_path).current == Settings(language="zh", close_action="quit")


@pytest.mark.parametrize("text", ["{not json", '{"version": 2}', "[1, 2]", '"text"'])
def test_unreadable_file_is_set_aside(tmp_path: Path, text: str) -> None:
    (tmp_path / SETTINGS_NAME).write_text(text, encoding="utf-8")
    store = SettingsStore(tmp_path)
    assert store.current == Settings()
    (kept,) = tmp_path.glob(f"{SETTINGS_NAME}.*.bad")
    assert kept.read_text(encoding="utf-8") == text
    store.update(language="en")  # the next write does not touch the copy
    assert kept.read_text(encoding="utf-8") == text


def test_failed_write_keeps_the_current_settings(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    (tmp_path / SETTINGS_NAME).mkdir()  # the rename onto it fails
    with pytest.raises(SettingsError) as raised:
        store.update(allow_lan=True)
    assert raised.value.code == "settings_unwritable"
    assert store.current == Settings()
