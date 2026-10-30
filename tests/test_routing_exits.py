"""The routing page's second-round parts: mesh outbounds (docs/mesh.md §5), the WARP exit and
the VPN Gate exit (docs/exits.md §2, §6), with the simulated host and the credential store in
memory."""

# The UI's Chinese text has fullwidth punctuation.
# ruff: noqa: RUF001

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QUrl
from pytestqt.qtbot import QtBot

from solaris.host.base import HostError
from solaris.models import plans
from solaris.models import vpngate as vpngate_list
from solaris.models.backup import Backups
from solaris.models.credentials import WARP_ACCOUNT
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.routing_exits import VpnGateExit, WarpExit
from solaris.viewmodels.rule_editor import RuleEditor
from tests.support.credentials import MemoryCredentials
from tests.support.simulated import (
    VPNGATE_SERVERS,
    SimulatedFetcher,
    SimulatedHost,
    seed_profiles,
)

NOW = datetime(2026, 10, 29, 12).timestamp()
MESH_CONFIG = """proxies:
  - {name: 家里, type: tailscale, auth-key: tskey-sample, hostname: solaris}
  - {name: 公司, type: zerotier, network-id: 8056c2e21c000001}
"""


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def rows(view_model: QObject, name: str) -> list[Any]:
    return list(prop(view_model, name).rows)


def warp(routing: RoutingViewModel) -> WarpExit:
    return routing._warp  # QML reads the property; Python has no converter for the type


def vpngate(routing: RoutingViewModel) -> VpnGateExit:
    return routing._vpngate


def editor(routing: RoutingViewModel) -> RuleEditor:
    return routing._editor


def host_of(view_models: ViewModels) -> SimulatedHost:
    host = view_models.host
    assert isinstance(host, SimulatedHost)
    return host


@pytest.fixture
def credentials() -> MemoryCredentials:
    return MemoryCredentials()


@pytest.fixture
def planned(qtbot: QtBot, tmp_path: Path, credentials: MemoryCredentials) -> Iterator[ViewModels]:
    """Routing with the default plan; the tunnel runs."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(
        SimulatedHost(), store, SimulatedFetcher(), lambda: NOW, credentials=credentials
    )
    built.start()
    routing = built.routing
    qtbot.waitUntil(lambda: prop(built.overview, "connected"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: prop(routing, "summary").endswith("27 个节点"))
    yield built
    built.shutdown()


def import_mesh(qtbot: QtBot, view_models: ViewModels, tmp_path: Path) -> None:
    source = tmp_path / "mesh.yaml"
    source.write_text(MESH_CONFIG, encoding="utf-8")
    view_models.profiles.importFile(QUrl.fromLocalFile(str(source)).toString())
    qtbot.waitUntil(lambda: prop(view_models.routing._mesh_networks, "hasOutbounds"))


def openai_to(qtbot: QtBot, routing: RoutingViewModel, target: str) -> None:
    openai = next(r for r in rows(routing.plan_rules(), "rows") if r.name == "OpenAI")
    dialog = editor(routing)
    dialog.openEdit(openai.ruleId)
    dialog.setTarget(target)
    with qtbot.waitSignal(dialog.saved):
        dialog.save()


def entry_target(host: SimulatedHost, routing: RoutingViewModel, name: str) -> str:
    rule = next(r for r in rows(routing.plan_rules(), "rows") if r.name == name)
    assert host.report is not None
    return next(r.proxy for r in host.report.rules if r.entry_id == rule.ruleId)


# -- mesh outbounds ----------------------------------------------------------------------------
def test_mesh_outbounds_stay_out_of_the_pool(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
    plan_groups = routing.plan_groups()
    import_mesh(qtbot, planned, tmp_path)
    assert [(m.name, m.caption, m.type) for m in rows(routing._mesh_networks, "outbounds")] == [
        ("家里", "Tailscale · 来自「mesh」", "Tailscale"),
        ("公司", "ZeroTier · 来自「mesh」", "ZeroTier"),
    ]
    # Not in the pool, the regions or 自动选择 (docs/mesh.md §3).
    assert prop(routing, "summary").endswith("节点池 27 个节点")
    assert rows(plan_groups, "builtin")[1].caption == "自动选择 · 27 个节点"
    assert [g.name for g in rows(plan_groups, "regions")] == ["香港", "日本", "新加坡", "美国"]
    assert host.effective is not None
    auto = next(g for g in host.effective["proxy-groups"] if g["name"] == "自动选择")
    assert "家里" not in auto["proxies"]
    # Rule targets of their own, after the groups and before the exits (docs/mesh.md §5).
    labels = [(o["label"], o["separator"]) for o in prop(routing.plan_rules(), "targetOptions")]
    assert labels[-6:] == [
        ("家里", True),
        ("公司", False),
        ("WARP 出口", True),
        ("VPN Gate 出口", False),
        ("直连", True),
        ("拒绝", False),
    ]


def test_common_rules_for_tailscale(qtbot: QtBot, planned: ViewModels, tmp_path: Path) -> None:
    routing, host = planned.routing, host_of(planned)
    plan_rules = routing.plan_rules()
    mesh = routing._mesh_networks
    import_mesh(qtbot, planned, tmp_path)
    tailscale = rows(mesh, "outbounds")[0]
    with qtbot.waitSignal(routing.applied):
        mesh.addOutboundRules(tailscale.meshId)
    added = rows(plan_rules, "rows")[:2]
    assert [(r.name, r.caption, r.target, r.warning) for r in added] == [
        ("IP 段（2 个）", "100.64.0.0/10、fd7a:115c:a1e0::/48 · no-resolve", "家里", ""),
        ("自定义域名（1 个）", "ts.net", "家里", ""),
    ]
    assert entry_target(host, routing, "IP 段（2 个）") == "家里"
    assert prop(routing, "undoText") == "已为「家里」添加 2 条规则"
    # Twice adds nothing new.
    mesh.addOutboundRules(tailscale.meshId)
    qtbot.wait(50)
    assert len(rows(plan_rules, "rows")) == 8
    # 使用它的规则 filters by the outbound.
    mesh.showOutboundRules(tailscale.meshId)
    assert [r.target for r in rows(plan_rules, "rows")] == ["家里", "家里"]
    assert prop(plan_rules, "targetFilterLabel") == "家里"


def test_zerotier_opens_the_ranges_with_its_target(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    mesh = routing._mesh_networks
    import_mesh(qtbot, planned, tmp_path)
    zerotier = rows(mesh, "outbounds")[1]
    dialog = editor(routing)
    with qtbot.waitSignal(dialog.opened):
        mesh.addOutboundRules(zerotier.meshId)
    assert (prop(dialog, "kind"), prop(dialog, "target")) == ("ip", zerotier.meshId)
    assert prop(dialog, "targetLabel") == "公司"
    dialog.setText("10.147.17.0/24")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert (rows(plan_rules, "rows")[0].target, rows(plan_rules, "rows")[0].warning) == ("公司", "")


def test_a_missing_mesh_outbound_rejects(qtbot: QtBot, planned: ViewModels, tmp_path: Path) -> None:
    routing, host = planned.routing, host_of(planned)
    import_mesh(qtbot, planned, tmp_path)
    tailscale = rows(routing._mesh_networks, "outbounds")[0]
    with qtbot.waitSignal(routing.applied):
        routing._mesh_networks.addOutboundRules(tailscale.meshId)
    mesh_profile = next(p for p in planned.library.snapshot().profiles if p.name == "mesh")
    profiles = planned.profiles
    with qtbot.waitSignal(profiles.routingChanged):
        profiles.setInPool(mesh_profile.id, False)
    qtbot.waitUntil(lambda: not prop(routing._mesh_networks, "hasOutbounds"))
    first = rows(routing.plan_rules(), "rows")[0]
    assert (first.target, first.warning) == ("家里", "不存在，已拒绝连接")
    assert entry_target(host, routing, "IP 段（2 个）") == "REJECT"


# -- the WARP exit -----------------------------------------------------------------------------
def test_warp_registers_and_its_rules_follow_the_account(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
    plan_rules = routing.plan_rules()
    exit_ = warp(routing)
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("未启用", "muted")
    assert prop(exit_, "caption") == "经由 节点选择 · MASQUE（HTTP/2）"
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    assert host.warp_calls == [("节点选择", "masque-h2", "")]
    assert prop(exit_, "accountTitle") == "已注册"
    assert prop(exit_, "accountDetail").endswith(" · 设备 a1b2c3d4")
    # The account is a credential: in the store, not in the database.
    stored = json.loads(credentials.secrets[WARP_ACCOUNT])
    assert stored["device_id"].startswith("a1b2c3d4")
    database = (tmp_path / "solaris.db").read_bytes()
    assert b"simulated-token" not in database
    # OpenAI → WARP 出口 goes out through it.
    openai_to(qtbot, routing, plans.WARP)
    openai = next(r for r in rows(plan_rules, "rows") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("WARP 出口", "")
    assert entry_target(host, routing, "OpenAI") == "WARP 出口"
    assert host.config is not None
    assert host.config.composition is not None
    assert host.config.composition.egress.warp is not None
    # Deleting the account only deletes this machine's copy; the rule rejects.
    with qtbot.waitSignal(routing.applied):
        exit_.confirmForget()
    assert WARP_ACCOUNT not in credentials.secrets
    openai = next(r for r in rows(plan_rules, "rows") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("WARP 出口", "不可用，已拒绝连接")
    assert entry_target(host, routing, "OpenAI") == "REJECT"
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("未注册", "warning")


def test_an_exit_turned_off_cannot_be_picked_and_rejects(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    plan_rules = routing.plan_rules()
    exit_ = warp(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    option = next(o for o in prop(plan_rules, "targetOptions") if o["value"] == plans.WARP)
    assert "enabled" not in option
    openai_to(qtbot, routing, plans.WARP)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(False)
    assert prop(exit_, "status") == "未启用"
    option = next(o for o in prop(plan_rules, "targetOptions") if o["value"] == plans.WARP)
    assert (option["note"], option["enabled"]) == ("未启用", False)
    assert entry_target(host, routing, "OpenAI") == "REJECT"
    assert host.config is not None
    assert host.config.composition is not None
    assert host.config.composition.egress.warp is None
    assert prop(exit_, "hasAccount")  # turning it off keeps the account


def test_registering_waits_for_the_connection(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host, overview = planned.routing, host_of(planned), planned.overview
    exit_ = warp(routing)
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    assert prop(exit_, "status") == "连接后注册"
    assert prop(exit_, "accountDetail") == "连接后经节点注册"
    assert host.warp_calls == []
    overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪", timeout=5000)
    assert len(host.warp_calls) == 1


def test_a_new_protocol_rekeys_the_account(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host, overview = planned.routing, host_of(planned), planned.overview
    exit_ = warp(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    device = host.warp_calls[0]
    exit_.setProtocol("wireguard")
    qtbot.waitUntil(lambda: len(host.warp_calls) == 2)
    assert host.warp_calls[1] == ("节点选择", "wireguard", "a1b2c3d4-0000-4000-8000-000000000000")
    assert planned.settings_store.current.warp_protocol == "wireguard"
    qtbot.waitUntil(lambda: prop(exit_, "caption") == "经由 节点选择 · WireGuard")
    qtbot.waitUntil(lambda: getattr(routing.warp_account(), "protocol", "") == "wireguard")
    # Chosen while stopped, the key follows once the tunnel runs.
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    exit_.setProtocol("masque-h3")
    assert "连接后换用 MASQUE（HTTP/3）" in prop(exit_, "accountDetail")
    assert len(host.warp_calls) == 2
    overview.toggleConnection()
    qtbot.waitUntil(lambda: len(host.warp_calls) == 3, timeout=5000)
    assert host.warp_calls[2][:2] == ("节点选择", "masque-h3")
    assert device[2] == ""  # the first one registered a new device


def test_a_failed_registration_says_why(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = warp(routing)
    host.warp_error = HostError("operation_failed", "error code: 1015")
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "error") != "")
    assert prop(exit_, "error") == "操作失败：error code: 1015"
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("不可用", "warning")
    exit_.register()
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    assert prop(exit_, "error") == ""


def test_the_via_group_goes_with_the_plan(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = warp(routing)
    assert [o["label"] for o in prop(exit_, "viaOptions")] == [
        "节点选择",
        "自动选择",
        "香港",
        "日本",
        "新加坡",
        "美国",
    ]
    with qtbot.waitSignal(routing.applied):
        exit_.setVia("region:JP")
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    assert host.warp_calls[-1][0] == "🇯🇵 日本"
    assert prop(exit_, "caption") == "经由 日本 · MASQUE（HTTP/2）"
    plan = routing.plan()
    assert plan is not None
    assert json.loads(plans.plan_json(plan, "zh"))["egress"] == {"warp": {"via": "region:JP"}}
    # The exit goes out through the group.
    openai_to(qtbot, routing, plans.WARP)
    assert host.effective is not None
    outbound = next(p for p in host.effective["proxies"] if p["name"] == "WARP 出口")
    assert outbound["dialer-proxy"] == "🇯🇵 日本"


def test_deleting_the_via_group_moves_the_exit_to_proxy(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_groups = routing.plan_groups()
    dialog = routing._group_editor
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName("香港精选")
    dialog.toggleRegion("HK")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    group = rows(plan_groups, "custom")[0]
    exit_ = warp(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setVia(group.groupId)
    assert prop(exit_, "viaLabel") == "香港精选"
    plan_groups.requestRemove(group.groupId)
    assert "WARP 出口经由它，删除后改经「节点选择」。" in prop(plan_groups, "removalText")
    with qtbot.waitSignal(routing.applied):
        plan_groups.confirmRemove()
    assert prop(exit_, "via") == plans.PROXY


def test_a_locked_credential_store_fails_closed(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials
) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = warp(routing)
    credentials.fail = True
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "error") != "")
    assert prop(exit_, "error") == "操作失败：the keychain is locked"
    assert prop(exit_, "status") == "不可用"
    openai_to(qtbot, routing, plans.WARP)
    assert entry_target(host, routing, "OpenAI") == "REJECT"


# -- the VPN Gate exit -------------------------------------------------------------------------
JP_HOSTS = [s.host_name for s in VPNGATE_SERVERS if s.country == "JP"]


def turn_on_vpngate(qtbot: QtBot, routing: RoutingViewModel) -> VpnGateExit:
    exit_ = vpngate(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "日本 5 台")
    return exit_


def picked(host: SimulatedHost) -> list[str]:
    assert host.config is not None
    assert host.config.composition is not None
    return [s.host_name for s in host.config.composition.egress.vpngate]


def test_vpngate_fetches_the_list_through_the_node_and_its_rules_follow(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = vpngate(routing)
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("未启用", "muted")
    assert prop(exit_, "caption") == "经由 节点选择 · 日本"
    exit_ = turn_on_vpngate(qtbot, routing)
    assert host.vpngate_calls == ["节点选择"]
    assert prop(exit_, "serversTitle") == "在用 5 台服务器"
    assert prop(exit_, "serversDetail") == "列表更新于 刚刚"
    servers = rows(exit_, "servers")
    assert [r.title for r in servers] == [f"日本 · {h}" for h in JP_HOSTS[:5]]
    assert (servers[0].detail, servers[0].delay) == ("DESKTOP-K7Q2's owner", "—")
    countries = [(c["label"], c["note"], c["checked"]) for c in prop(exit_, "countryOptions")]
    assert countries[:3] == [("日本", "6", True), ("韩国", "2", False), ("俄罗斯", "1", False)]
    # The last country chosen cannot be taken away.
    assert not prop(exit_, "countryOptions")[0]["enabled"]
    assert prop(exit_, "countriesLabel") == "日本"
    # A rule to the exit goes through the servers, best score first, each through the node.
    openai_to(qtbot, routing, plans.VPNGATE)
    openai = next(r for r in rows(routing.plan_rules(), "rows") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("VPN Gate 出口", "")
    assert entry_target(host, routing, "OpenAI") == "VPN Gate 出口"
    assert picked(host) == JP_HOSTS[:5]
    assert host.effective is not None
    group = next(g for g in host.effective["proxy-groups"] if g["name"] == "VPN Gate 出口")
    assert group["type"] == "load-balance"
    outbound = next(p for p in host.effective["proxies"] if p["name"] == group["proxies"][0])
    assert outbound["dialer-proxy"] == "节点选择"
    # The proxies page lists the exit's group with its servers.
    groups = planned.runner.submit(host.list_groups()).result(timeout=5)
    listed = next(g for g in groups if g.name == "VPN Gate 出口")
    assert listed.type == "LoadBalance"
    first = listed.proxies[0]
    assert (first.name, first.type) == (f"VPN Gate JP {JP_HOSTS[0]}", "OpenVPN")
    # The list is this machine's: in the state table, never in a backup.
    plan = routing.plan()
    assert plan is not None
    assert json.loads(plans.plan_json(plan, "zh"))["egress"] == {
        "vpngate": {"via": "proxy", "countries": ["JP"], "count": 5, "mode": "per-site"}
    }
    database = ProfileStore(tmp_path).database
    stored = vpngate_list.load(database)
    assert (len(stored.servers), stored.picked) == (len(VPNGATE_SERVERS), tuple(JP_HOSTS[:5]))
    assert "vpn381472650" not in json.dumps(Backups(database).snapshot().document)


def test_vpngate_waits_for_the_connection(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host, overview = planned.routing, host_of(planned), planned.overview
    exit_ = vpngate(routing)
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    # Turned off, the exit never fetches the list, however often the tunnel starts.
    overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    exit_._tick()
    qtbot.wait(50)
    assert host.vpngate_calls == []
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("连接后获取", "muted")
    assert prop(exit_, "serversDetail") == "连接后经节点获取服务器列表"
    assert host.vpngate_calls == []
    overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(exit_, "status") == "日本 5 台", timeout=5000)
    assert len(host.vpngate_calls) == 1
    # A fresh list is not fetched again for the next connection.
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    qtbot.wait(50)
    assert len(host.vpngate_calls) == 1


def test_a_failed_server_gives_up_its_position(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host, library = planned.routing, host_of(planned), planned.library
    exit_ = turn_on_vpngate(qtbot, routing)
    openai_to(qtbot, routing, plans.VPNGATE)
    first = picked(host)
    reloads = host.reloads
    host.fail_vpngate(first[1])
    with qtbot.waitSignal(routing.applied):
        exit_._tick()  # the two-minute check
    # Only its position changes hands; the sites hashed to the others keep their servers.
    assert picked(host) == [first[0], JP_HOSTS[5], *first[2:]]
    assert host.reloads == reloads + 1
    assert first[1] in library.routing_snapshot().vpngate.failing(library.now())
    assert len(host.vpngate_calls) == 1
    # Fewer than half working: the list is fetched again, though not within ten minutes.
    now = library.now()
    for name in picked(host)[:3]:
        host.fail_vpngate(name)
    with qtbot.waitSignal(routing.applied):
        exit_._tick()
    assert len(picked(host)) == 2  # the two left in Japan
    assert len(host.vpngate_calls) == 1
    library._core.clock = lambda: now + 11 * 60
    renamed = (replace(s, host_name=s.host_name + "2") for s in VPNGATE_SERVERS)
    host.vpngate_servers = (*VPNGATE_SERVERS, *renamed)
    for name in picked(host):
        host.fail_vpngate(name)
    with qtbot.waitSignal(routing.applied):
        exit_._tick()
    assert len(host.vpngate_calls) == 2
    assert len(picked(host)) == 5
    assert all(h.endswith("2") for h in picked(host))


def test_a_failed_list_says_why_and_rejects(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = vpngate(routing)
    host.vpngate_error = HostError("operation_failed", "VPN Gate answered 503: busy")
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "error") != "")
    assert prop(exit_, "error") == "操作失败：VPN Gate answered 503: busy"
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("不可用", "warning")
    # Without servers the exit's rules reject, rather than go out through the node (D21).
    openai_to(qtbot, routing, plans.VPNGATE)
    openai = next(r for r in rows(routing.plan_rules(), "rows") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("VPN Gate 出口", "不可用，已拒绝连接")
    assert entry_target(host, routing, "OpenAI") == "REJECT"
    exit_.refresh()
    qtbot.waitUntil(lambda: prop(exit_, "status") == "日本 5 台")
    assert prop(exit_, "error") == ""
    assert entry_target(host, routing, "OpenAI") == "VPN Gate 出口"


def test_vpngate_settings_go_with_the_plan(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = turn_on_vpngate(qtbot, routing)
    openai_to(qtbot, routing, plans.VPNGATE)
    with qtbot.waitSignal(routing.applied):
        exit_.setCount("2")
    assert picked(host) == JP_HOSTS[:2]
    with qtbot.waitSignal(routing.applied):
        exit_.toggleCountry("KR")
    assert prop(exit_, "severalCountries")
    assert prop(exit_, "caption") == "经由 节点选择 · 日本、韩国"
    assert picked(host) == JP_HOSTS[:2]  # still usable, so they stay
    with qtbot.waitSignal(routing.applied):
        exit_.toggleCountry("JP")
    assert picked(host) == [s.host_name for s in VPNGATE_SERVERS if s.country == "KR"]
    assert prop(exit_, "status") == "韩国 2 台"
    exit_.toggleCountry("KR")  # the last country stays
    assert prop(exit_, "caption") == "经由 节点选择 · 韩国"
    with qtbot.waitSignal(routing.applied):
        exit_.setMode("single")
    assert host.effective is not None
    group = next(g for g in host.effective["proxy-groups"] if g["name"] == "VPN Gate 出口")
    assert group["type"] == "fallback"
    with qtbot.waitSignal(routing.applied):
        exit_.toggleCountry("FR")
    with qtbot.waitSignal(routing.applied):
        exit_.toggleCountry("KR")
    # No server in France: the exit has none, and its rules reject.
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("不可用", "warning")
    assert prop(exit_, "serversDetail").startswith("所选国家暂无可用的服务器 · ")
    assert entry_target(host, routing, "OpenAI") == "REJECT"
    plan = routing.plan()
    assert plan is not None
    assert json.loads(plans.plan_json(plan, "zh"))["egress"]["vpngate"] == {
        "via": "proxy",
        "countries": ["FR"],
        "count": 2,
        "mode": "single",
    }


def test_deleting_the_via_group_moves_vpngate_to_proxy(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    plan_groups = routing.plan_groups()
    exit_ = vpngate(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setVia("region:JP")
    exit_ = turn_on_vpngate(qtbot, routing)
    assert host.vpngate_calls == ["🇯🇵 日本"]
    assert prop(exit_, "caption") == "经由 日本 · 日本"
    dialog = routing._group_editor
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName("香港精选")
    dialog.toggleRegion("HK")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    group = rows(plan_groups, "custom")[0]
    with qtbot.waitSignal(routing.applied):
        exit_.setVia(group.groupId)
    plan_groups.requestRemove(group.groupId)
    assert "VPN Gate 出口经由它，删除后改经「节点选择」。" in prop(plan_groups, "removalText")
    with qtbot.waitSignal(routing.applied):
        plan_groups.confirmRemove()
    assert prop(exit_, "via") == plans.PROXY
