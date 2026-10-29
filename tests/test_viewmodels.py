from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from mihomo_desktop.host.base import HostError, RunOptions
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Streams
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.composition import ViewModels, compose
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles


def prop(view_model: QObject, name: str) -> Any:
    """Read a Qt property the way QML does (pyright types them as Property objects)."""
    return view_model.property(name)


NOW = datetime(2026, 10, 29, 12).timestamp()


@pytest.fixture
def view_models(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW)
    built.start()
    yield built
    built.shutdown()


def test_overview_shows_the_running_tunnel(qtbot: QtBot, view_models: ViewModels) -> None:
    overview = view_models.overview
    qtbot.waitUntil(lambda: prop(overview, "nodeName") == "香港 01")
    assert prop(overview, "connected")
    assert prop(overview, "statusTitle") == "已连接"
    assert prop(overview, "nodeDelay") == "68 ms"
    assert len(prop(overview, "downPoints")) == 40


def test_overview_actions(qtbot: QtBot, view_models: ViewModels) -> None:
    overview, host = view_models.overview, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    overview.setMode("global")
    qtbot.waitUntil(lambda: prop(overview, "mode") == "global")
    # A switch changes the running tunnel and is kept for the next connection.
    overview.setSystemProxy(True)
    qtbot.waitUntil(lambda: prop(overview, "systemProxyEnabled"))
    assert view_models.settings_store.current.system_proxy
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    assert prop(overview, "statusTitle") == "未连接"
    assert prop(overview, "recent") == []
    assert prop(overview, "downRate") == "—"

    # While stopped the switches show, and change, what the next connection asks for.
    assert (prop(overview, "tunEnabled"), prop(overview, "systemProxyEnabled")) == (False, True)
    overview.setTun(True)
    qtbot.waitUntil(lambda: prop(overview, "tunEnabled"))
    assert view_models.settings_store.current.tun
    assert host.reloads == 0
    overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    assert host.config is not None
    assert host.config.options == RunOptions(True, True, mixed_port=7890)


def test_development_service_explains_the_switches(qtbot: QtBot, tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(development=True), store, SimulatedFetcher(), lambda: NOW)
    built.start()
    try:
        overview = built.overview
        qtbot.waitUntil(lambda: prop(overview, "connected"))
        overview.setTun(True)
        qtbot.waitUntil(lambda: prop(overview, "switchError") == "开发模式不支持 TUN 模式")
        assert prop(overview, "statusDetail") == "开发模式不支持 TUN 模式"
        assert not prop(overview, "tunEnabled")
        assert not built.settings_store.current.tun  # a refusal is not kept

        overview.setSystemProxy(True)
        qtbot.waitUntil(lambda: prop(overview, "switchError") == "开发模式不支持系统代理")
        overview.setMode("direct")  # another action clears the hint
        qtbot.waitUntil(lambda: prop(overview, "mode") == "direct")
        assert prop(overview, "switchError") == ""
    finally:
        built.shutdown()


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


def test_unreachable_service_is_reported(qtbot: QtBot, tmp_path: Path) -> None:
    async def unavailable() -> Streams:
        raise HostError("service_unavailable", "no service")

    built = compose(ServiceHost(unavailable), ProfileStore(tmp_path), SimulatedFetcher())
    built.start()
    try:
        qtbot.waitUntil(lambda: prop(built.overview, "statusDetail") == "未能连接到服务")
        qtbot.waitUntil(lambda: prop(built.shell, "serviceText") == "服务不可用")
        assert not prop(built.profiles, "hasActive")
        assert prop(built.profiles, "summary") == "0 个配置"
    finally:
        built.shutdown()


def test_profiles_page_lists_the_samples(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles = view_models.profiles
    assert prop(profiles, "summary") == "3 个配置"
    a, b, c = prop(profiles, "profiles")
    assert (a["name"], a["active"], b["active"]) == ("示例订阅 A", True, False)
    assert a["source"] == "订阅 · sub.example.com · 9 个节点"
    assert (a["usage"], a["expiry"], a["updated"]) == ("38.2 / 200 GB", "2026-12-31", "2 小时前")
    assert a["hasQuota"]
    assert abs(a["usedFraction"] - 0.191) < 0.001
    assert b["updated"] == "3 天前"
    assert c["source"] == "本地文件 · config.yaml · 9 个节点"
    assert (c["usage"], c["expiry"], c["refreshLabel"]) == ("—", "—", "重新读取")
    assert not c["hasQuota"]

    # The overview's profile group and the sidebar read the active profile.
    assert prop(profiles, "activeName") == "示例订阅 A"
    assert prop(profiles, "activeDetail") == "sub.example.com · 2 小时前更新"
    assert prop(profiles, "activeUsage") == "38.2 / 200 GB"
    assert prop(profiles, "activeExpiry") == "2026-12-31 到期"


def test_switching_profiles_reloads_the_tunnel(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles, host = view_models.profiles, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    with qtbot.waitSignal(profiles.applied):
        profiles.use("sample-b")
    assert prop(profiles, "activeName") == "示例订阅 B"
    assert [row["active"] for row in prop(profiles, "profiles")] == [False, True, False]
    assert host.reloads == 1

    profiles.updateProfile("sample-b")
    assert prop(profiles, "activeUpdating")
    assert prop(profiles, "profiles")[1]["updated"] == "正在更新"
    qtbot.waitUntil(lambda: not prop(profiles, "activeUpdating"))
    assert prop(profiles, "profiles")[1]["updated"] == "刚刚"
    assert host.reloads == 2


def test_importing_from_a_link(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles = view_models.profiles
    with qtbot.waitSignal(profiles.imported):
        profiles.importUrl("https://new.example.com/sub", "新订阅", True)
    assert prop(profiles, "summary") == "4 个配置"
    row = prop(profiles, "profiles")[-1]
    assert (row["name"], row["active"], row["autoUpdate"]) == ("新订阅", False, True)
    assert prop(profiles, "autoUpdateLabel") == "每 12 小时自动更新"


def test_import_failures(qtbot: QtBot, view_models: ViewModels, tmp_path: Path) -> None:
    profiles = view_models.profiles
    profiles.importUrl("not a link", "", True)
    qtbot.waitUntil(lambda: prop(profiles, "importError") == "链接无效")
    assert prop(profiles, "summary") == "3 个配置"  # link failures stay in the form

    bad = tmp_path / "bad.yaml"
    bad.write_text("just: text\n", encoding="utf-8")
    profiles.importFile(bad.as_uri())
    qtbot.waitUntil(lambda: prop(profiles, "hasError"))
    expected = "配置无效：the config defines no proxies"  # noqa: RUF001
    assert prop(profiles, "summary") == expected
    profiles.clearImportError()
    assert prop(profiles, "importError") == ""


def test_removal_waits_for_confirmation(
    qtbot: QtBot, view_models: ViewModels, tmp_path: Path
) -> None:
    profiles = view_models.profiles
    with qtbot.waitSignal(profiles.removalRequested):
        profiles.requestRemove("sample-b")
    assert prop(profiles, "removalTitle") == "删除「示例订阅 B」？"  # noqa: RUF001
    assert prop(profiles, "removalText") == "配置文件会从本机删除，无法恢复。"  # noqa: RUF001

    # Cancelling removes nothing, and a late confirmation does not either.
    profiles.cancelRemove()
    profiles.confirmRemove()
    qtbot.wait(50)
    assert prop(profiles, "summary") == "3 个配置"
    assert (tmp_path / "profiles" / "sample-b.yaml").exists()

    with qtbot.waitSignal(profiles.removalRequested):
        profiles.requestRemove("sample-b")
    profiles.confirmRemove()
    qtbot.waitUntil(lambda: prop(profiles, "summary") == "2 个配置")
    assert not (tmp_path / "profiles" / "sample-b.yaml").exists()


def test_removing_the_profile_in_use(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles, overview = view_models.profiles, view_models.overview
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    # While the tunnel runs it is refused without asking.
    with qtbot.assertNotEmitted(profiles.removalRequested, wait=100):
        profiles.requestRemove("sample-a")
        qtbot.waitUntil(lambda: prop(profiles, "hasError"))
    assert prop(profiles, "summary") == "正在使用的配置需要先断开才能删除"

    # Once stopped, the confirmation names the profile that takes over.
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    with qtbot.waitSignal(profiles.removalRequested):
        profiles.requestRemove("sample-a")
    assert prop(profiles, "removalText") == (
        "这是当前使用的配置，删除后改用「示例订阅 B」。配置文件会从本机删除，无法恢复。"  # noqa: RUF001
    )
    profiles.confirmRemove()
    qtbot.waitUntil(lambda: prop(profiles, "activeName") == "示例订阅 B")
    assert prop(profiles, "summary") == "2 个配置"


def test_editing_an_override(qtbot: QtBot, view_models: ViewModels, tmp_path: Path) -> None:
    profiles, host = view_models.profiles, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    with qtbot.waitSignal(profiles.overrideOpened):
        profiles.editOverride("sample-b")
    assert prop(profiles, "overrideTitle") == "编辑「示例订阅 B」的覆写"
    assert prop(profiles, "overrideText") == ""

    # The core's rejection stays in the editor and nothing is stored.
    with qtbot.assertNotEmitted(profiles.overrideSaved, wait=100):
        profiles.saveOverride("mode: [\n")
        qtbot.waitUntil(lambda: prop(profiles, "overrideError") != "")
    assert prop(profiles, "overrideError").startswith("配置无效：override 1: ")  # noqa: RUF001
    assert not prop(profiles, "overrideSaving")
    assert not (tmp_path / "overrides" / "sample-b.yaml").exists()

    # Another profile's override is stored without reloading the tunnel.
    with qtbot.waitSignal(profiles.overrideSaved):
        profiles.saveOverride("mode: direct\n")
    assert prop(profiles, "overrideError") == ""
    row = prop(profiles, "profiles")[1]
    assert row["hasOverride"]
    assert row["source"] == "订阅 · nodes.example.org · 9 个节点 · 已覆写"
    assert host.reloads == 0

    with qtbot.waitSignal(profiles.overrideOpened):
        profiles.editOverride("sample-b")
    assert prop(profiles, "overrideText") == "mode: direct\n"


def test_editing_the_global_override_reloads(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles, host = view_models.profiles, view_models.host
    assert isinstance(host, SimulatedHost)
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    assert prop(profiles, "globalOverrideDetail") == "所有配置共用 · 未设置"
    with qtbot.waitSignal(profiles.overrideOpened):
        profiles.editGlobalOverride()
    assert prop(profiles, "overrideTitle") == "编辑全局覆写"

    with qtbot.waitSignals([profiles.overrideSaved, profiles.applied]):
        profiles.saveOverride("mode: global\n")
    assert prop(profiles, "hasGlobalOverride")
    assert prop(profiles, "globalOverrideDetail") == "所有配置共用 · 已设置"
    assert host.reloads == 1
    assert host.config is not None
    assert host.config.overrides == ("mode: global\n",)


def test_removal_mentions_the_override(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles = view_models.profiles
    with qtbot.waitSignal(profiles.overrideOpened):
        profiles.editOverride("sample-c")
    with qtbot.waitSignal(profiles.overrideSaved):
        profiles.saveOverride("mode: direct\n")
    with qtbot.waitSignal(profiles.removalRequested):
        profiles.requestRemove("sample-c")
    assert prop(profiles, "removalText") == "配置文件和它的覆写会从本机删除，无法恢复。"  # noqa: RUF001
