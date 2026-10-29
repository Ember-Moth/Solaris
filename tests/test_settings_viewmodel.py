from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from mihomo_desktop.desktop.login import LoginItemError, LoginItemStatus
from mihomo_desktop.host.base import RunOptions
from mihomo_desktop.models.settings import SETTINGS_NAME, JsonSettingsStore, Settings
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.composition import ViewModels, compose
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

NOW = datetime(2026, 10, 29, 12).timestamp()


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


class FakeLoginItem:
    """Records what would be registered; never touches the system."""

    def __init__(self, *, enabled: bool = False, approval: bool = False) -> None:
        self.available = True
        self.enabled = enabled
        self.approval = approval  # registering needs the user's approval (macOS)
        self.fail = False
        self.opened = 0

    def status(self) -> LoginItemStatus:
        return LoginItemStatus(self.enabled, needs_approval=self.enabled and self.approval)

    def set_enabled(self, enabled: bool) -> LoginItemStatus:
        if self.fail:
            raise LoginItemError("access is denied")
        self.enabled = enabled
        return self.status()

    def open_settings(self) -> None:
        self.opened += 1


@pytest.fixture
def login_item() -> FakeLoginItem:
    return FakeLoginItem()


@pytest.fixture
def view_models(qtbot: QtBot, tmp_path: Path, login_item: FakeLoginItem) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW, login_item=login_item)
    built.start()
    yield built
    built.shutdown()


def test_about_shows_the_versions(qtbot: QtBot, view_models: ViewModels) -> None:
    settings = view_models.settings
    qtbot.waitUntil(lambda: prop(settings, "coreVersion") == "mihomo v1.19.33")
    assert prop(settings, "serviceVersion") == "simulated"
    assert prop(settings, "protocolVersion") == "v1"
    assert prop(settings, "license") == "GPL-3.0-only"
    assert prop(settings, "projectUrl") == "https://github.com/Ember-Moth/mihomo-desktop"


def test_choices_are_stored(qtbot: QtBot, view_models: ViewModels, tmp_path: Path) -> None:
    settings = view_models.settings
    labels = [o["label"] for o in prop(settings, "languageOptions")]
    assert labels == ["跟随系统", "简体中文", "English"]
    assert [o["value"] for o in prop(settings, "appearanceOptions")] == ["system", "light", "dark"]

    with qtbot.waitSignal(settings.languageChanged):
        settings.setLanguage("en")
    with qtbot.waitSignal(settings.appearanceChanged):
        settings.setAppearance("dark")
    with qtbot.assertNotEmitted(settings.appearanceChanged):
        settings.setAppearance("dark")  # unchanged
        settings.setAppearance("sepia")  # not a choice
    settings.setCloseAction("quit")

    assert (prop(settings, "language"), prop(settings, "appearance")) == ("en", "dark")
    assert prop(settings, "closeAction") == "quit"
    stored = JsonSettingsStore(tmp_path).current  # read back from the file
    assert (stored.language, stored.appearance, stored.close_action) == ("en", "dark", "quit")


def test_the_page_follows_changes_made_elsewhere(qtbot: QtBot, view_models: ViewModels) -> None:
    """A change that does not come from the page, e.g. a restored backup, still shows."""
    settings, desktop = view_models.settings, view_models.desktop
    with (
        qtbot.waitSignal(settings.languageChanged),
        qtbot.waitSignal(settings.mixedPortChanged),
        qtbot.waitSignal(desktop.closeActionChanged),
    ):
        view_models.settings_store.update(language="en", mixed_port=7897, close_action="quit")
    assert prop(settings, "language") == "en"
    assert prop(settings, "mixedPort") == 7897
    assert prop(settings, "closeAction") == "quit"


def test_unwritable_settings_are_reported(
    qtbot: QtBot, view_models: ViewModels, tmp_path: Path
) -> None:
    settings = view_models.settings
    (tmp_path / SETTINGS_NAME).mkdir()  # nothing can replace the settings file
    with qtbot.assertNotEmitted(settings.languageChanged):
        settings.setLanguage("en")
    assert prop(settings, "language") == "system"
    assert prop(settings, "error").startswith("无法保存设置：")  # noqa: RUF001


def test_port_and_lan_reload_the_running_tunnel(qtbot: QtBot, view_models: ViewModels) -> None:
    settings, host = view_models.settings, view_models.host
    assert isinstance(host, SimulatedHost)
    assert prop(settings, "mixedPort") == 7890

    for text in ("80", "70000", "", "abc"):
        settings.setMixedPort(text)
        assert prop(settings, "portError") == "端口应在 1024–65535 之间"  # noqa: RUF001
    assert host.reloads == 0

    with qtbot.waitSignal(settings.mixedPortChanged):
        settings.setMixedPort("7897")
    assert prop(settings, "portError") == ""
    assert host.reloads == 1
    assert host.config is not None
    assert host.config.options.mixed_port == 7897
    assert view_models.settings_store.current.mixed_port == 7897

    settings.setAllowLan(True)
    qtbot.waitUntil(lambda: prop(settings, "allowLan"))
    assert host.config.options == RunOptions(True, False, 7897, True)


def test_a_port_the_core_cannot_open_is_not_kept(qtbot: QtBot, view_models: ViewModels) -> None:
    settings, host = view_models.settings, view_models.host
    assert isinstance(host, SimulatedHost)
    host.busy_ports.add(1080)
    settings.setMixedPort("1080")
    qtbot.waitUntil(lambda: prop(settings, "portError") != "")
    message = "配置未能生效：listen tcp 127.0.0.1:1080: bind: address already in use"  # noqa: RUF001
    assert prop(settings, "portError") == message
    assert prop(settings, "mixedPort") == 7890
    assert view_models.settings_store.current.mixed_port == 7890


def test_port_while_stopped_is_only_stored(qtbot: QtBot, view_models: ViewModels) -> None:
    settings, host = view_models.settings, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    view_models.overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(view_models.overview, "connected"))
    with qtbot.waitSignal(settings.mixedPortChanged):
        settings.setMixedPort("7899")
    assert host.reloads == 0
    view_models.overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    assert host.config is not None
    assert host.config.options.mixed_port == 7899


def test_launch_at_login(qtbot: QtBot, view_models: ViewModels, login_item: FakeLoginItem) -> None:
    settings = view_models.settings
    qtbot.waitUntil(lambda: prop(settings, "launchAtLoginAvailable"))  # read at start
    settings.setLaunchAtLogin(True)
    qtbot.waitUntil(lambda: prop(settings, "launchAtLogin"))
    assert login_item.enabled
    assert view_models.settings_store.current.launch_at_login

    # The user removed it in the system's settings: the page follows the system.
    login_item.enabled = False
    settings.refreshLoginItem()
    qtbot.waitUntil(lambda: not prop(settings, "launchAtLogin"))
    assert not view_models.settings_store.current.launch_at_login

    login_item.fail = True
    settings.setLaunchAtLogin(True)
    qtbot.waitUntil(lambda: prop(settings, "launchAtLoginDetail") != "")
    expected = "无法更改登录时打开：access is denied"  # noqa: RUF001
    assert prop(settings, "launchAtLoginDetail") == expected
    assert not prop(settings, "launchAtLogin")


@pytest.mark.parametrize("login_item", [FakeLoginItem(approval=True)])
def test_launch_at_login_awaiting_approval(
    qtbot: QtBot, view_models: ViewModels, login_item: FakeLoginItem
) -> None:
    settings = view_models.settings
    qtbot.waitUntil(lambda: prop(settings, "launchAtLoginAvailable"))
    settings.setLaunchAtLogin(True)
    qtbot.waitUntil(lambda: prop(settings, "launchAtLoginNeedsApproval"))
    assert prop(settings, "launchAtLoginDetail") == "需要在系统设置的登录项中允许"
    settings.openLoginItemSettings()
    qtbot.waitUntil(lambda: login_item.opened == 1)


def test_launch_at_login_from_source(qtbot: QtBot, tmp_path: Path) -> None:
    built = compose(SimulatedHost(), ProfileStore(tmp_path), SimulatedFetcher())
    built.start()
    try:
        settings = built.settings
        assert not prop(settings, "launchAtLoginAvailable")
        assert prop(settings, "launchAtLoginDetail") == "仅安装版可用"
        settings.setLaunchAtLogin(True)  # ignored
        assert not prop(settings, "launchAtLogin")
        assert built.settings_store.current == Settings()
    finally:
        built.shutdown()
