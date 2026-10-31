"""Extension maintenance with simulated hosts and in-memory credentials only."""

import asyncio
import threading

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from solaris.host.base import TAKEOVER_TUN, HostError
from solaris.models.backup import Backups
from tests.support.credentials import MemoryKeyring
from tests.support.simulated import SimulatedHost
from tests.support.window import Loaded, kind


@pytest.mark.parametrize("takeover", [TAKEOVER_TUN], indirect=True)
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
def test_deactivation_confirms_and_keeps_profiles_settings_and_credentials(
    qtbot: QtBot, loaded: Loaded, keyring: MemoryKeyring
) -> None:
    models = loaded.view_models
    host = models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: models.overview.property("connected"))
    models.shell.setPage("settings")
    button = loaded.item(
        lambda item: kind(item) == "AppButton" and item.property("text") in ("停用…", "Deactivate…")
    )
    button.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(lambda: loaded.on_screen(button))
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: loaded.focused_text() in ("取消", "Cancel"))
    loaded.key(qtbot, Qt.Key.Key_Return)
    assert host.deactivations == 0

    store = models.library._core.store
    backups = Backups(store.database)
    before = backups.snapshot().digest
    settings = models.settings_store.current
    configs = {profile.id: store.read_config(profile.id) for profile in store.load().profiles}
    keyring.secrets[("Solaris", "maintenance-test")] = "kept"
    secrets = dict(keyring.secrets)
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: loaded.focused_text() in ("取消", "Cancel"))
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() in ("停用", "Deactivate")
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: models.system_extension.property("state") == "done")
    qtbot.waitUntil(lambda: not models.overview.property("connected"))
    assert host.deactivations == 1
    assert backups.snapshot().digest == before
    assert models.settings_store.current == settings
    assert {
        profile.id: store.read_config(profile.id) for profile in store.load().profiles
    } == configs
    assert keyring.secrets == secrets
    assert loaded.warnings == []


@pytest.mark.parametrize("takeover", [TAKEOVER_TUN], indirect=True)
@pytest.mark.parametrize(
    ("code", "state"),
    [
        ("extension_deactivation_pending", "pending"),
        ("extension_busy", "busy"),
        ("cancelled", "cancelled"),
        ("timeout", "failed"),
    ],
)
def test_pending_or_failed_deactivation_is_retryable_and_never_claims_completion(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch, code: str, state: str
) -> None:
    models = loaded.view_models
    host, extension = models.host, models.system_extension
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: models.overview.property("connected"))
    original = host.deactivate_extension

    async def pending(timeout: float = 60) -> None:
        await host.stop()
        raise HostError(code, "native detail")

    monkeypatch.setattr(host, "deactivate_extension", pending)
    extension.deactivate()
    qtbot.waitUntil(lambda: extension.property("state") == state)
    assert "重启" not in extension.property("detail")
    assert not extension.property("busy")
    qtbot.waitUntil(lambda: not models.overview.property("connected"))
    monkeypatch.setattr(host, "deactivate_extension", original)
    extension.deactivate()
    qtbot.waitUntil(lambda: extension.property("state") == "done")
    qtbot.waitUntil(lambda: models.overview.property("canToggleConnection"))
    models.overview.setConnected(True)
    qtbot.waitUntil(
        lambda: models.overview.property("connected") and not models.overview.property("busy")
    )
    assert extension.property("state") == "ready"
    assert loaded.warnings == []


@pytest.mark.parametrize("takeover", [TAKEOVER_TUN], indirect=True)
def test_maintenance_prevents_duplicate_requests_and_connect_races(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = loaded.view_models
    host, extension = models.host, models.system_extension
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: models.overview.property("connected"))
    entered, release = threading.Event(), threading.Event()
    original = host.deactivate_extension

    async def blocked(timeout: float = 60) -> None:
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        await original(timeout)

    monkeypatch.setattr(host, "deactivate_extension", blocked)
    extension.deactivate()
    qtbot.waitUntil(entered.is_set)
    try:
        assert extension.property("busy")
        assert models.overview.property("busy")
        assert not models.overview.property("canToggleConnection")
        extension.deactivate()
        models.overview.toggleConnection()
        assert host._running
    finally:
        release.set()
    qtbot.waitUntil(lambda: extension.property("state") == "done")
    assert host.deactivations == 1
    qtbot.waitUntil(lambda: not models.overview.property("busy"))
    assert not models.overview.property("busy")
    assert loaded.warnings == []


def test_other_hosts_do_not_offer_extension_maintenance(qtbot: QtBot, loaded: Loaded) -> None:
    models = loaded.view_models
    host = models.host
    assert isinstance(host, SimulatedHost)
    assert not models.system_extension.property("available")
    models.system_extension.deactivate()
    assert host.deactivations == 0
    models.shell.setPage("settings")
    assert not loaded.overlay_items(lambda item: item.property("text") in ("停用…", "Deactivate…"))
