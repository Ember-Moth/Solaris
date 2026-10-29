from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from mihomo_desktop.host.base import HostError
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Streams
from mihomo_desktop.models.profiles import NoProfiles
from mihomo_desktop.viewmodels.composition import ViewModels, compose
from tests.support.simulated import SampleProfiles, SimulatedHost


def prop(view_model: QObject, name: str) -> Any:
    """Read a Qt property the way QML does (pyright types them as Property objects)."""
    return view_model.property(name)


@pytest.fixture
def view_models(qtbot: QtBot) -> Iterator[ViewModels]:
    built = compose(SimulatedHost(), SampleProfiles())
    built.start()
    yield built
    built.shutdown()


def test_overview_shows_the_running_tunnel(qtbot: QtBot, view_models: ViewModels) -> None:
    overview = view_models.overview
    qtbot.waitUntil(lambda: prop(overview, "nodeName") == "香港 01")
    assert prop(overview, "connected")
    assert prop(overview, "statusTitle") == "已连接"
    assert prop(overview, "nodeDelay") == "68 ms"
    assert prop(overview, "profileUsage") == "38.2 GB / 200 GB"
    assert len(prop(overview, "downPoints")) == 40


def test_overview_actions(qtbot: QtBot, view_models: ViewModels) -> None:
    overview = view_models.overview
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    overview.setMode("global")
    qtbot.waitUntil(lambda: prop(overview, "mode") == "global")
    overview.setSystemProxy(True)
    qtbot.waitUntil(lambda: prop(overview, "systemProxyEnabled"))
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    assert prop(overview, "statusTitle") == "未连接"
    assert prop(overview, "recent") == []
    assert prop(overview, "downRate") == "—"


def test_selecting_a_proxy_updates_both_screens(qtbot: QtBot, view_models: ViewModels) -> None:
    proxies, overview = view_models.proxies, view_models.overview
    qtbot.waitUntil(lambda: prop(proxies, "currentGroup") == "节点选择")
    assert prop(proxies, "currentSelectable")
    proxies.selectProxy("香港 02")
    qtbot.waitUntil(lambda: prop(overview, "nodeName") == "香港 02")
    selected = [node["name"] for node in prop(proxies, "nodes") if node["selected"]]
    assert selected == ["香港 02"]

    proxies.selectGroup("自动选择")
    assert not prop(proxies, "currentSelectable")
    proxies.selectProxy("日本 01")  # ignored: the core picks for URLTest groups
    proxies.setSort("delay")
    assert prop(proxies, "nodes")[0]["name"] == "香港 02"


def test_unreachable_service_is_reported(qtbot: QtBot) -> None:
    async def unavailable() -> Streams:
        raise HostError("service_unavailable", "no service")

    built = compose(ServiceHost(unavailable), NoProfiles())
    built.start()
    try:
        qtbot.waitUntil(lambda: prop(built.overview, "statusDetail") == "未能连接到服务")
        qtbot.waitUntil(lambda: prop(built.shell, "serviceText") == "服务不可用")
        assert not prop(built.overview, "hasProfile")
        assert prop(built.shell, "profileName") == "未导入配置"
    finally:
        built.shutdown()
