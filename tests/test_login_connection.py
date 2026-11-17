"""Windows login connection policy; no real task, TUN or system proxy is changed."""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pytestqt.qtbot import QtBot

from solaris.desktop.login import LoginItemError, LoginItemStatus, UnavailableLoginItem
from solaris.host.base import HostError, HostStatus, TunnelConfig
from solaris.models.settings import DatabaseSettingsStore
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles


class Login(UnavailableLoginItem):
    available = True

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.release = threading.Event()
        self.release.set()
        self.error = False

    def status(self) -> LoginItemStatus:
        assert self.release.wait(5)
        if self.error:
            raise LoginItemError("cannot read task")
        return LoginItemStatus(self.enabled)


class Host(SimulatedHost):
    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self.starts = 0
        self.error = ""
        self.start_error = False

    async def get_status(self) -> HostStatus:
        if self.error:
            raise HostError(self.error, "test service not ready")
        return await super().get_status()

    async def start(self, config: TunnelConfig) -> Any:
        self.starts += 1
        if self.start_error:
            raise HostError("apply_failed", "test connection failed")
        return await super().start(config)


@pytest.fixture
def build(qtbot: QtBot, tmp_path: Path) -> Callable[..., Any]:
    @contextmanager
    def opened(
        *,
        enabled: bool = True,
        login_launch: bool = True,
        cached: bool = False,
        profiles: bool = True,
        host: Host | None = None,
        login: Login | None = None,
    ) -> Iterator[tuple[ViewModels, Host]]:
        store = ProfileStore(tmp_path)
        if profiles:
            seed_profiles(store, 1000)
        settings = DatabaseSettingsStore(store.database)
        settings.update(launch_at_login=cached)
        host, login = host or Host(), login or Login(enabled)
        vm = compose(
            host,
            store,
            SimulatedFetcher(),
            settings=settings,
            login_item=login,
            connect_at_login=login_launch,
            updater=offline_updater(tmp_path),
        )
        vm.start()
        try:
            yield vm, host
        finally:
            login.release.set()
            vm.shutdown()

    return opened


@pytest.mark.parametrize(
    ("enabled", "login_launch", "cached", "starts"),
    [
        (True, True, False, 1),
        (False, True, True, 0),
        (True, False, True, 0),
    ],
)
def test_only_enabled_login_launch_connects(
    qtbot: QtBot,
    build: Callable[..., Any],
    enabled: bool,
    login_launch: bool,
    cached: bool,
    starts: int,
) -> None:
    with build(enabled=enabled, login_launch=login_launch, cached=cached) as (vm, host):
        qtbot.waitUntil(
            lambda: vm.overview._snapshot is not None and not vm.overview._login_pending
        )
        qtbot.waitUntil(lambda: host.starts == starts)
        if starts:
            qtbot.waitUntil(lambda: bool(vm.overview.property("connected")))
            vm.overview.setConnected(False)
            qtbot.waitUntil(
                lambda: not vm.overview.property("connected") and not vm.overview.property("busy")
            )
        vm.overview.refresh()
        qtbot.wait(100)
        assert host.starts == starts


def test_login_waits_for_service(qtbot: QtBot, build: Callable[..., Any]) -> None:
    host = Host()
    host.error = "service_unavailable"
    with build(host=host) as (vm, _):
        qtbot.waitUntil(lambda: vm.overview._refresh_code == "service_unavailable")
        assert host.starts == 0
        host.error = ""
        vm.overview.refresh()
        qtbot.waitUntil(lambda: bool(vm.overview.property("connected")))
        assert host.starts == 1


@pytest.mark.parametrize("state", ["running", "recovering", "owned", "recovery_failed"])
def test_login_does_not_override_existing_tunnel(
    qtbot: QtBot,
    build: Callable[..., Any],
    state: str,
) -> None:
    host = Host()
    host._running = state == "running"
    host.recovery_state = {"recovering": "waiting_network", "recovery_failed": "failed"}.get(
        state, "none"
    )
    host.error = "owned_by_other_user" if state == "owned" else ""
    with build(host=host) as (vm, _):
        qtbot.waitUntil(lambda: not vm.overview._login_pending)
        host.error, host.recovery_state, host._running = "", "none", False
        vm.overview.refresh()
        qtbot.wait(100)
        assert host.starts == 0


@pytest.mark.parametrize("cancel", ["disconnect", "quit", "disable", "timeout"])
def test_pending_login_connection_can_be_cancelled(
    qtbot: QtBot,
    build: Callable[..., Any],
    cancel: str,
) -> None:
    login, host = Login(), Host()
    login.release.clear()
    with build(login=login, host=host, cached=True) as (vm, _):
        qtbot.waitUntil(lambda: vm.overview._snapshot is not None)
        if cancel == "disconnect":
            vm.overview.setConnected(False)
        elif cancel == "quit":
            assert not vm.overview.disconnect_to_quit(1, lambda: None, lambda _: None)
        elif cancel == "disable":
            vm.settings_store.update(launch_at_login=False)
            login.enabled = False
        else:
            vm.overview._login_deadline = 0
        login.release.set()
        qtbot.waitUntil(lambda: vm.overview._login_checked)
        vm.overview.refresh()
        qtbot.wait(100)
        assert host.starts == 0


@pytest.mark.parametrize("failure", ["connect", "task", "no_profile"])
def test_login_failure_is_reported_without_retry(
    qtbot: QtBot,
    build: Callable[..., Any],
    failure: str,
) -> None:
    host, login = Host(), Login()
    host.start_error = failure == "connect"
    login.error = failure == "task"
    with build(host=host, login=login, profiles=failure != "no_profile") as (vm, _):
        qtbot.waitUntil(lambda: bool(vm.overview._connection_error))
        vm.overview.refresh()
        qtbot.wait(100)
        assert host.starts == (1 if failure == "connect" else 0)
