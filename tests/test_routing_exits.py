"""The routing page's second-round parts: mesh outbounds (docs/mesh.md §5) and the WARP exit
(docs/exits.md §2, §6), with the simulated host and the credential store in memory."""

# The UI's Chinese text has fullwidth punctuation.
# ruff: noqa: RUF001

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QUrl
from pytestqt.qtbot import QtBot

from solaris.host.base import HostError
from solaris.models import plans
from solaris.models.credentials import WARP_ACCOUNT
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.routing_exits import WarpExit
from solaris.viewmodels.rule_editor import RuleEditor
from tests.support.credentials import MemoryCredentials
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

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
    qtbot.waitUntil(lambda: prop(view_models.routing, "hasMesh"))


def openai_to(qtbot: QtBot, routing: RoutingViewModel, target: str) -> None:
    openai = next(r for r in rows(routing, "rules") if r.name == "OpenAI")
    dialog = editor(routing)
    dialog.openEdit(openai.ruleId)
    dialog.setTarget(target)
    with qtbot.waitSignal(dialog.saved):
        dialog.save()


def entry_target(host: SimulatedHost, routing: RoutingViewModel, name: str) -> str:
    rule = next(r for r in rows(routing, "rules") if r.name == name)
    assert host.report is not None
    return next(r.proxy for r in host.report.rules if r.entry_id == rule.ruleId)


# -- mesh outbounds ----------------------------------------------------------------------------
def test_mesh_outbounds_stay_out_of_the_pool(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
    import_mesh(qtbot, planned, tmp_path)
    assert [(m.name, m.caption, m.type) for m in rows(routing, "meshOutbounds")] == [
        ("家里", "Tailscale · 来自「mesh」", "Tailscale"),
        ("公司", "ZeroTier · 来自「mesh」", "ZeroTier"),
    ]
    # Not in the pool, the regions or 自动选择 (docs/mesh.md §3).
    assert prop(routing, "summary").endswith("节点池 27 个节点")
    assert rows(routing, "builtinGroups")[1].caption == "自动选择 · 27 个节点"
    assert [g.name for g in rows(routing, "regionGroups")] == ["香港", "日本", "新加坡", "美国"]
    assert host.effective is not None
    auto = next(g for g in host.effective["proxy-groups"] if g["name"] == "自动选择")
    assert "家里" not in auto["proxies"]
    # Rule targets of their own, after the groups and before the exits (docs/mesh.md §5).
    labels = [(o["label"], o["separator"]) for o in prop(routing, "targetOptions")]
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
    import_mesh(qtbot, planned, tmp_path)
    tailscale = rows(routing, "meshOutbounds")[0]
    with qtbot.waitSignal(routing.applied):
        routing.addMeshRules(tailscale.meshId)
    added = rows(routing, "rules")[:2]
    assert [(r.name, r.caption, r.target, r.warning) for r in added] == [
        ("IP 段（2 个）", "100.64.0.0/10、fd7a:115c:a1e0::/48 · no-resolve", "家里", ""),
        ("自定义域名（1 个）", "ts.net", "家里", ""),
    ]
    assert entry_target(host, routing, "IP 段（2 个）") == "家里"
    assert prop(routing, "undoText") == "已为「家里」添加 2 条规则"
    # Twice adds nothing new.
    routing.addMeshRules(tailscale.meshId)
    qtbot.wait(50)
    assert len(rows(routing, "rules")) == 8
    # 使用它的规则 filters by the outbound.
    routing.showMeshRules(tailscale.meshId)
    assert [r.target for r in rows(routing, "rules")] == ["家里", "家里"]
    assert prop(routing, "targetFilterLabel") == "家里"


def test_zerotier_opens_the_ranges_with_its_target(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing = planned.routing
    import_mesh(qtbot, planned, tmp_path)
    zerotier = rows(routing, "meshOutbounds")[1]
    dialog = editor(routing)
    with qtbot.waitSignal(dialog.opened):
        routing.addMeshRules(zerotier.meshId)
    assert (prop(dialog, "kind"), prop(dialog, "target")) == ("ip", zerotier.meshId)
    assert prop(dialog, "targetLabel") == "公司"
    dialog.setText("10.147.17.0/24")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert (rows(routing, "rules")[0].target, rows(routing, "rules")[0].warning) == ("公司", "")


def test_a_missing_mesh_outbound_rejects(qtbot: QtBot, planned: ViewModels, tmp_path: Path) -> None:
    routing, host = planned.routing, host_of(planned)
    import_mesh(qtbot, planned, tmp_path)
    tailscale = rows(routing, "meshOutbounds")[0]
    with qtbot.waitSignal(routing.applied):
        routing.addMeshRules(tailscale.meshId)
    mesh_profile = next(p for p in planned.library.snapshot().profiles if p.name == "mesh")
    profiles = planned.profiles
    with qtbot.waitSignal(profiles.routingChanged):
        profiles.setInPool(mesh_profile.id, False)
    qtbot.waitUntil(lambda: not prop(routing, "hasMesh"))
    first = rows(routing, "rules")[0]
    assert (first.target, first.warning) == ("家里", "不存在，已拒绝连接")
    assert entry_target(host, routing, "IP 段（2 个）") == "REJECT"


# -- the WARP exit -----------------------------------------------------------------------------
def test_warp_registers_and_its_rules_follow_the_account(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
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
    openai = next(r for r in rows(routing, "rules") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("WARP 出口", "")
    assert entry_target(host, routing, "OpenAI") == "WARP 出口"
    assert host.config is not None
    assert host.config.composition is not None
    assert host.config.composition.egress.warp is not None
    # Deleting the account only deletes this machine's copy; the rule rejects.
    with qtbot.waitSignal(routing.applied):
        exit_.confirmForget()
    assert WARP_ACCOUNT not in credentials.secrets
    openai = next(r for r in rows(routing, "rules") if r.name == "OpenAI")
    assert (openai.target, openai.warning) == ("WARP 出口", "不可用，已拒绝连接")
    assert entry_target(host, routing, "OpenAI") == "REJECT"
    assert (prop(exit_, "status"), prop(exit_, "statusTone")) == ("未注册", "warning")


def test_an_exit_turned_off_cannot_be_picked_and_rejects(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, host_of(planned)
    exit_ = warp(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(True)
    qtbot.waitUntil(lambda: prop(exit_, "status") == "已就绪")
    option = next(o for o in prop(routing, "targetOptions") if o["value"] == plans.WARP)
    assert "enabled" not in option
    openai_to(qtbot, routing, plans.WARP)
    with qtbot.waitSignal(routing.applied):
        exit_.setActive(False)
    assert prop(exit_, "status") == "未启用"
    option = next(o for o in prop(routing, "targetOptions") if o["value"] == plans.WARP)
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
    dialog = routing._group_editor
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName("香港精选")
    dialog.toggleRegion("HK")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    group = rows(routing, "customGroups")[0]
    exit_ = warp(routing)
    with qtbot.waitSignal(routing.applied):
        exit_.setVia(group.groupId)
    assert prop(exit_, "viaLabel") == "香港精选"
    routing.requestRemoveGroup(group.groupId)
    assert "WARP 出口经由它，删除后改经「节点选择」。" in prop(routing, "groupRemovalText")
    with qtbot.waitSignal(routing.applied):
        routing.confirmRemoveGroup()
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
