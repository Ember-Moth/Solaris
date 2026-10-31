"""Drive macOS setup with a simulated host; never activate an extension or open Settings."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt
from pytestqt.qtbot import QtBot

from solaris.host.base import HostError, TunnelConfig
from solaris.viewmodels import overview as overview_module
from tests.support.simulated import SimulatedHost
from tests.support.window import Loaded, find_all


class Activation:
    def __init__(self, start: Callable[[TunnelConfig], Coroutine[Any, Any, Any]]) -> None:
        self.start = start
        self.error: HostError | None = HostError("extension_needs_approval", "native detail")
        self.calls = 0
        self.hold = False

    async def __call__(self, config: TunnelConfig) -> Any:
        self.calls += 1
        while self.hold:
            await asyncio.sleep(0.01)
        if self.error is not None:
            raise self.error
        return await self.start(config)


@pytest.fixture
def activation(qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch) -> Activation:
    overview, host = loaded.view_models.overview, loaded.view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: overview.property("connected"))
    overview.setConnected(False)
    qtbot.waitUntil(lambda: not overview.property("busy"))
    activation = Activation(host.start)
    monkeypatch.setattr(host, "start", activation)
    # The native settings launcher is an external effect, even on a development Mac.
    monkeypatch.setattr(overview_module, "open_login_settings", lambda: None)
    return activation


def guide(loaded: Loaded) -> QObject:
    return next(
        child
        for child in loaded.window.findChildren(QObject)
        if child.metaObject().className().startswith("ExtensionSetupDialog_")
    )


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
def test_approval_from_the_tray_shows_a_guide_and_continues_only_on_request(
    qtbot: QtBot, loaded: Loaded, activation: Activation
) -> None:
    overview = loaded.view_models.overview
    loaded.window.setWidth(960)
    loaded.window.setHeight(640)
    loaded.view_models.shell.setPage("settings")
    loaded.window.hide()
    overview.toggleConnection()
    dialog = guide(loaded)
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert loaded.window.isVisible()
    assert loaded.view_models.shell.property("currentPage") == "overview"
    assert overview.property("extensionSetup") == "approval"
    assert not overview.property("connectionFailed")
    assert not overview.property("connectionOn")
    assert "native detail" not in overview.property("connectionDetail")
    assert loaded.focused_text() in ("继续连接", "Continue connecting")
    # All of the guide's text and actions fit at the supported minimum window size.
    for item in find_all(dialog.property("contentItem"), lambda item: bool(item.property("text"))):
        assert loaded.on_screen(item)
        assert not item.property("truncated")

    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert not dialog.property("visible")
    overview.refresh()
    qtbot.waitUntil(lambda: not overview.property("busy"))
    assert overview.property("extensionSetup") == "approval"
    assert activation.calls == 1
    assert loaded.focused().metaObject().className().startswith("AppSwitch_")

    guide_button = loaded.item(lambda item: item.property("text") in ("安装指引…", "Setup guide…"))
    guide_button.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: dialog.property("opened"))
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert loaded.focused() is guide_button
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: dialog.property("opened"))
    loaded.key(qtbot, Qt.Key.Key_Return)  # still not approved: the same guide stays open
    qtbot.waitUntil(lambda: activation.calls == 2 and not overview.property("busy"))
    assert dialog.property("visible")
    assert not overview.property("connectionFailed")

    activation.error = None
    activation.hold = True
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: activation.calls == 3)
    loaded.key(qtbot, Qt.Key.Key_Return)
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert activation.calls == 3  # no duplicate connect, and no cancellation implied
    assert dialog.property("visible")
    activation.hold = False
    qtbot.waitUntil(lambda: overview.property("connected") and not overview.property("busy"))
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert overview.property("extensionSetup") == ""
    assert overview.property("connectionDetail") == ""
    assert loaded.warnings == []


@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_pending_activation_allows_retry_and_recovers_without_restarting(
    qtbot: QtBot, loaded: Loaded, activation: Activation
) -> None:
    overview = loaded.view_models.overview
    loaded.window.setWidth(960)
    loaded.window.setHeight(640)
    activation.error = HostError("extension_needs_reboot", "native detail")
    overview.setConnected(True)
    dialog = guide(loaded)
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert overview.property("extensionSetup") == "pending"
    assert not overview.property("connectionFailed")
    assert dialog.property("title") in ("网络扩展尚未就绪", "Network extension not ready")
    assert loaded.focused_text() in ("重试连接", "Retry connection")
    displayed = [overview.property("statusTitle"), overview.property("connectionDetail")]
    for item in find_all(dialog.property("contentItem"), lambda item: bool(item.property("text"))):
        assert loaded.on_screen(item)
        assert not item.property("truncated")
        displayed.append(item.property("text"))
    assert not any(term in " ".join(displayed).lower() for term in ("重启", "restart", "reboot"))

    # Closing the guide leaves the connection switch usable. Another click asks the
    # host again, rather than trusting the previous result for this entire app session.
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert not dialog.property("visible")
    overview.toggleConnection()
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert activation.calls == 2
    assert overview.property("extensionSetup") == "pending"

    # The system can now complete activation: the guide's default button retries too.
    activation.error = None
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: overview.property("connected") and not overview.property("busy"))
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert activation.calls == 3
    assert overview.property("extensionSetup") == ""
    assert overview.property("connectionDetail") == ""
    assert loaded.warnings == []


def test_settings_launcher_failure_keeps_manual_instructions(
    qtbot: QtBot, loaded: Loaded, activation: Activation, monkeypatch: pytest.MonkeyPatch
) -> None:
    overview = loaded.view_models.overview
    overview.setConnected(True)
    dialog = guide(loaded)
    qtbot.waitUntil(lambda: dialog.property("opened"))

    def unavailable() -> None:
        raise OSError("ServiceManagement unavailable")

    monkeypatch.setattr(overview_module, "open_login_settings", unavailable)
    button = loaded.overlay_items(lambda i: i.property("text") == "打开系统设置")[0]
    loaded.click(qtbot, button)
    qtbot.waitUntil(lambda: bool(overview.property("extensionSettingsError")))
    assert dialog.property("visible")
    assert activation.calls == 1
    assert not overview.property("connectionFailed")
    path = "系统设置 › 通用 › 登录项与扩展"  # noqa: RUF001
    assert loaded.overlay_items(lambda i: i.property("text") == path)

    monkeypatch.setattr(overview_module, "open_login_settings", lambda: None)
    loaded.click(qtbot, button)
    qtbot.waitUntil(lambda: not overview.property("extensionSettingsOpening"))
    assert overview.property("extensionSettingsError") == ""
    assert activation.calls == 1  # opening Settings does not continue the connection
    assert loaded.warnings == []


def test_an_unrelated_connect_failure_leaves_the_guide_and_shows_the_reason(
    qtbot: QtBot, loaded: Loaded, activation: Activation
) -> None:
    overview = loaded.view_models.overview
    overview.setConnected(True)
    dialog = guide(loaded)
    qtbot.waitUntil(lambda: dialog.property("opened"))
    activation.error = HostError("apply_failed", "port in use")
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    assert overview.property("extensionSetup") == ""
    assert overview.property("connectionFailed")
    assert overview.property("connectionDetail") == "配置未能生效：port in use"  # noqa: RUF001
    assert loaded.warnings == []
