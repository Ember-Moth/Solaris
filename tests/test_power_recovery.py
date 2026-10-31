"""Recovery UI with a simulated service; never suspend the machine or change its network."""

import pytest
from pytestqt.qtbot import QtBot

from tests.support.simulated import SimulatedHost
from tests.support.window import Loaded, find, kind


def recovering(qtbot: QtBot, loaded: Loaded, state: str, reason: str = "") -> SimulatedHost:
    overview, host = loaded.view_models.overview, loaded.view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: overview.property("connected"))
    host._running = False
    host.recovery_state, host.recovery_error = state, reason
    overview.refresh()
    qtbot.waitUntil(lambda: not overview.property("connected"))
    return host


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("state", ["sleeping", "waiting_network", "connecting"])
@pytest.mark.parametrize("action", ["switch", "tray"])
def test_owner_can_cancel_wake_recovery_from_the_switch_or_tray(
    qtbot: QtBot, loaded: Loaded, state: str, action: str
) -> None:
    host = recovering(qtbot, loaded, state)
    overview = loaded.view_models.overview
    assert overview.property("recovering")
    assert overview.property("connectionOn")
    assert overview.property("canToggleConnection")
    assert not overview.property("connectionFailed")
    assert overview.property("connectLabel") in ("断开", "Disconnect")
    if action == "tray":
        overview.toggleConnection()
    else:
        page = loaded.item(lambda item: kind(item) == "OverviewPage")
        switch = find(page, lambda item: kind(item) == "AppSwitch")
        assert switch is not None
        assert switch.isEnabled()
        loaded.click(qtbot, switch)
    qtbot.waitUntil(lambda: not overview.property("busy") and not overview.property("recovering"))
    assert host.recovery_state == "none"
    assert not overview.property("connectionOn")
    assert overview.property("connectionDetail") == ""
    assert loaded.warnings == []


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_failed_wake_recovery_explains_the_failure_and_allows_a_new_connection(
    qtbot: QtBot, loaded: Loaded
) -> None:
    recovering(qtbot, loaded, "failed", "network_unavailable")
    overview = loaded.view_models.overview
    assert not overview.property("recovering")
    assert not overview.property("connectionOn")
    assert overview.property("connectionFailed")
    assert overview.property("canToggleConnection")
    titles = ("恢复连接失败", "Couldn’t restore the connection")  # noqa: RUF001
    assert overview.property("statusTitle") in titles
    assert "network_unavailable" not in overview.property("connectionDetail")
    overview.setConnected(True)
    qtbot.waitUntil(lambda: overview.property("connected") and not overview.property("busy"))
    assert not overview.property("connectionFailed")
    assert overview.property("connectionDetail") == ""
    assert loaded.warnings == []
