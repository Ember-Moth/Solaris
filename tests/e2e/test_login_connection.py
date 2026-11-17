"""Login-triggered connection through the real service and a local HTTP endpoint.

The login item is a double. Native Windows tasks, logon events and TUN need Windows
acceptance; this test runs the actual Qt/Python -> Go -> mihomo connection path.
"""

from pathlib import Path

import pytest
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from scripts.dev import DevService
from solaris.app import load_ui
from solaris.host.base import TAKEOVER_NONE
from solaris.host.service import ServiceHost
from solaris.models.profiles import KIND_FILE, Profile, ProfileList
from solaris.models.routing import MODE_PROFILE, RoutingStore
from solaris.models.settings import DatabaseSettingsStore
from solaris.models.store import ProfileStore
from solaris.models.subscription import UrlFetcher
from solaris.viewmodels.composition import compose
from tests.e2e.app import DIRECT_PROFILE, App, Network
from tests.e2e.servers import free_port
from tests.e2e.unix import unix_opener
from tests.support.releases import offline_updater
from tests.test_login_connection import Login

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(("enabled", "login_launch"), [(True, True), (False, True), (True, False)])
def test_login_connection_with_real_core(
    qtbot: QtBot,
    tmp_path: Path,
    service: DevService,
    network: Network,
    enabled: bool,
    login_launch: bool,
) -> None:
    data = tmp_path / "login-data"
    store = ProfileStore(data)
    store.write_config("local", DIRECT_PROFILE)
    store.save(ProfileList((Profile(id="local", name="Local", kind=KIND_FILE),), "local"))
    RoutingStore(store.database).update_state(mode=MODE_PROFILE)
    settings = DatabaseSettingsStore(store.database)
    settings.update(mixed_port=free_port(), tun=False, system_proxy=False)
    vm = compose(
        ServiceHost(unix_opener(str(service.socket)), takeover=TAKEOVER_NONE),
        store,
        UrlFetcher(),
        settings=settings,
        login_item=Login(enabled),
        connect_at_login=login_launch,
        start_hidden=login_launch,
        updater=offline_updater(data),
    )
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    try:
        assert load_ui(engine, vm)
        (window,) = engine.rootObjects()
        assert isinstance(window, QQuickWindow)
        app = App(qtbot, vm, window, warnings, service, network, data)
        vm.start()
        if enabled and login_launch:
            app.until(lambda: bool(vm.overview.property("connected")))
        else:
            app.until(lambda: vm.overview._snapshot is not None and not vm.overview._login_pending)
            assert not vm.overview.property("connected")
            app.connect()
        assert app.curl(network.direct.url()) == "direct"
        vm.overview.setConnected(False)
        app.until(
            lambda: not vm.overview.property("connected") and not vm.overview.property("busy")
        )
        vm.overview.refresh()
        qtbot.wait(100)
        assert not vm.overview.property("connected")
        assert not warnings
    finally:
        vm.shutdown()
        for root in engine.rootObjects():
            root.deleteLater()
