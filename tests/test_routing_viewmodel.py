"""The routing page's ViewModel and the add-rule dialog (docs/routing.md §4.3, §4.4, §5.4)."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.rule_editor import RuleEditor
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

NOW = datetime(2026, 10, 29, 12).timestamp()


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def rows(view_model: QObject, name: str) -> list[Any]:
    return list(prop(view_model, name).rows)


@pytest.fixture
def view_models(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW)
    built.start()
    yield built
    built.shutdown()


@pytest.fixture
def planned(qtbot: QtBot, view_models: ViewModels) -> ViewModels:
    """Routing with the default plan, which the first switch makes."""
    routing = view_models.routing
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: prop(routing, "summary").endswith("27 个节点"))
    qtbot.waitUntil(lambda: prop(view_models.profiles, "summary").endswith("27 个节点"))
    return view_models


def editor(routing: RoutingViewModel) -> RuleEditor:
    return routing._editor  # QML reads the property; Python has no converter for the type


def test_the_profiles_own_rules_are_shown_read_only(qtbot: QtBot, view_models: ViewModels) -> None:
    routing = view_models.routing
    profile_routes = routing._profile_routes
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    assert prop(routing, "mode") == "profile"
    assert prop(routing, "summary") == "使用「示例订阅 A」自带的规则"
    assert prop(routing, "usingLabel") == "「示例订阅 A」自带的规则"
    assert prop(routing, "sidebarLabel") == "「示例订阅 A」的规则"
    routing.refresh()
    qtbot.waitUntil(lambda: prop(profile_routes, "running"))
    assert [(o["label"], o["note"], o["enabled"]) for o in prop(profile_routes, "options")] == [
        ("示例订阅 A", "1 条规则", True),
        ("示例订阅 B", "1 条规则", True),
        ("本地配置", "1 条规则", True),
    ]
    assert [(r.match, r.target) for r in rows(profile_routes, "rules")] == [("MATCH", "节点选择")]
    groups = rows(profile_routes, "groups")
    assert (groups[0].name, groups[0].caption) == ("节点选择", "Selector · 10 个成员")
    assert prop(profile_routes, "ruleCount") == "1 条"
    profile_routes.setQuery("geosite")
    assert prop(profile_routes, "rulesEmpty")
    assert prop(profile_routes, "ruleCount") == "匹配 0 条"


def test_my_plan_starts_from_the_template(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, planned.host
    plan_rules = routing.plan_rules()
    plan_groups = routing.plan_groups()
    assert isinstance(host, SimulatedHost)
    assert host.config is not None
    assert host.config.composition is not None
    assert prop(routing, "summary") == "使用「日常」 · 节点池 27 个节点"
    assert prop(routing, "usingLabel") == "「日常」方案"
    assert [(r.name, r.target, r.warning) for r in rows(plan_rules, "rows")] == [
        ("广告", "拒绝", ""),
        ("私有网络", "直连", ""),
        ("OpenAI", "美国", ""),
        ("Anthropic", "美国", ""),
        ("Gemini", "美国", ""),
        ("国内常用", "直连", ""),
    ]
    assert rows(plan_rules, "rows")[1].caption == "GEOSITE private · GEOIP private"
    assert (prop(plan_rules, "fallbackLabel"), prop(plan_rules, "fallbackWarning")) == (
        "节点选择",
        "",
    )
    # The target dropdown's fixed sections.
    options = [(o["label"], o["separator"]) for o in prop(plan_rules, "targetOptions")]
    assert options == [
        ("节点选择", False),
        ("自动选择", False),
        ("香港", True),
        ("日本", False),
        ("新加坡", False),
        ("美国", False),
        ("WARP 出口", True),
        ("VPN Gate 出口", False),
        ("直连", True),
        ("拒绝", False),
    ]
    # The plan has turned neither exit on (docs/exits.md §6).
    for exit_id in ("egress:warp", "egress:vpngate"):
        found = next(o for o in prop(plan_rules, "targetOptions") if o["value"] == exit_id)
        assert (found["note"], found["enabled"]) == ("未启用", False)
    assert [(g.name, g.caption) for g in rows(plan_groups, "builtin")] == [
        ("节点选择", "手动选择 · 自动选择、4 个地区组、直连"),
        ("自动选择", "自动选择 · 27 个节点"),
    ]
    assert [g.name for g in rows(plan_groups, "regions")] == ["香港", "日本", "新加坡", "美国"]
    # The other pages: the pool, the sources of the nodes and the names of the entries.
    assert prop(planned.profiles, "summary") == "3 份订阅 · 节点池 27 个节点"
    assert prop(planned.proxies, "showSource")
    assert planned.connections._entries["fallback"] == "其他流量"


def test_a_rule_can_be_turned_off(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, planned.host
    plan_rules = routing.plan_rules()
    assert isinstance(host, SimulatedHost)
    reloads = host.reloads
    first = rows(plan_rules, "rows")[0]
    with qtbot.waitSignal(routing.applied):
        plan_rules.setRuleEnabled(first.id, False)
    assert rows(plan_rules, "rows")[0].caption == "GEOSITE category-ads-all · 已停用"
    assert host.reloads == reloads + 1
    assert host.report is not None
    assert first.id not in {rule.entry_id for rule in host.report.rules}


def test_moves_wait_and_go_as_one_change(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, planned.host
    plan_rules = routing.plan_rules()
    assert isinstance(host, SimulatedHost)
    reloads = host.reloads
    names = [r.name for r in rows(plan_rules, "rows")]
    plan_rules.moveRule(rows(plan_rules, "rows")[0].id, 2)  # 广告 below OpenAI
    plan_rules.moveRuleBy(rows(plan_rules, "rows")[0].id, 1)  # 私有网络 one down
    shown = [r.name for r in rows(plan_rules, "rows")]
    assert shown == ["OpenAI", "私有网络", "广告", *names[3:]]
    assert host.reloads == reloads  # nothing stored yet
    with qtbot.waitSignal(routing.applied, timeout=3000):
        pass
    assert host.reloads == reloads + 1
    assert [r.name for r in rows(plan_rules, "rows")] == shown


def test_a_removal_can_be_undone(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    openai = rows(plan_rules, "rows")[2]
    with qtbot.waitSignal(routing.applied):
        plan_rules.removeRule(openai.id)
    assert prop(routing, "canUndo")
    assert prop(routing, "undoText") == "已删除「OpenAI」"
    assert prop(routing, "undoName") == "撤销删除「OpenAI」"
    assert "OpenAI" not in [r.name for r in rows(plan_rules, "rows")]
    with qtbot.waitSignal(routing.applied):
        routing.undo()
    assert [r.name for r in rows(plan_rules, "rows")][2] == "OpenAI"
    assert not prop(routing, "canUndo")

    # The hint goes after a while, or with the next change.
    routing._undo_timer.setInterval(50)
    with qtbot.waitSignal(routing.applied):
        plan_rules.removeRule(openai.id)
    qtbot.waitUntil(lambda: not prop(routing, "canUndo"))
    with qtbot.waitSignal(routing.applied):
        plan_rules.removeRule(rows(plan_rules, "rows")[0].id)
    with qtbot.waitSignal(routing.applied):
        plan_rules.setFallback("DIRECT")
    assert not prop(routing, "canUndo")
    assert prop(plan_rules, "fallbackLabel") == "直连"


def test_adding_services_puts_them_first(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    dialog = editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    assert (prop(dialog, "title"), prop(dialog, "kind")) == ("添加规则", "service")
    assert not prop(dialog, "canSave")
    services = {row.id: row for row in rows(dialog, "services")}
    assert services["openai"].added  # in the template
    assert services["openai"].note == "已添加"
    assert [r.category for r in rows(dialog, "services")][:3] == ["基础", "基础", "基础"]
    dialog.setServiceQuery("tele")
    assert [r.name for r in rows(dialog, "services")] == ["Telegram"]
    dialog.toggleService("telegram")
    dialog.setServiceQuery("")
    dialog.toggleService("bbc")
    # The first pick suggests the target; BBC's region has no nodes in the pool.
    assert prop(dialog, "target") == "proxy"
    assert prop(dialog, "serviceSummary") == "已选 2 个：Telegram、BBC iPlayer · 将添加 2 条规则"  # noqa: RUF001
    dialog.setTarget("region:JP")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert [(r.name, r.target) for r in rows(routing.plan_rules(), "rows")][:3] == [
        ("Telegram", "日本"),
        ("BBC iPlayer", "日本"),
        ("广告", "拒绝"),
    ]


def test_a_target_without_nodes_is_flagged(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    dialog = editor(routing)
    dialog.openAdd()
    dialog.toggleService("bbc")
    assert prop(dialog, "target") == "region:GB"
    assert prop(dialog, "targetLabel") == "英国"
    assert prop(dialog, "targetHint") == "英国暂时没有节点，暂走节点选择"  # noqa: RUF001
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    bbc = rows(routing.plan_rules(), "rows")[0]
    assert (bbc.target, bbc.warning) == ("英国", "无节点，暂走节点选择")  # noqa: RUF001


def test_domains_need_every_line_valid(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    dialog = editor(routing)
    dialog.openAdd()
    dialog.setKind("domain")
    assert prop(dialog, "textSummary").startswith("每行一个。")
    dialog.setText("intranet.example.com\n=git.example.com\n~tracker\nexa mple.com")
    assert prop(dialog, "textSummary") == "识别出 3 个，其中 1 行无效"  # noqa: RUF001
    assert prop(dialog, "textError") == "第 4 行无效：exa mple.com"  # noqa: RUF001
    assert not prop(dialog, "canSave")
    dialog.setText("intranet.example.com\n=git.example.com\n~tracker")
    assert prop(dialog, "canSave")
    dialog.setTarget("DIRECT")
    # Another kind keeps its own text.
    dialog.setKind("ip")
    assert prop(dialog, "ipText") == ""
    dialog.setKind("domain")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    rule = rows(plan_rules, "rows")[0]
    assert (rule.name, rule.target) == ("自定义域名（3 个）", "直连")  # noqa: RUF001
    assert rule.caption == "intranet.example.com、=git.example.com、~tracker"

    # Editing keeps its place and its id.
    dialog.openEdit(rule.id)
    assert (prop(dialog, "title"), prop(dialog, "domainText")) == (
        "编辑规则",
        "intranet.example.com\n=git.example.com\n~tracker",
    )
    dialog.setText("intranet.example.com")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    edited = rows(plan_rules, "rows")[0]
    assert (edited.id, edited.name) == (rule.id, "自定义域名（1 个）")  # noqa: RUF001


def test_ip_ranges_and_categories(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    dialog = editor(routing)
    dialog.openAdd()
    dialog.setKind("ip")
    dialog.setText("10.0.0.0/8\n2001:db8::/32")
    dialog.setNoResolve(False)
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert rows(plan_rules, "rows")[0].caption == "10.0.0.0/8、2001:db8::/32"

    dialog.openAdd()
    dialog.setKind("category")
    qtbot.waitUntil(lambda: len(rows(dialog, "categories")) > 1000)
    dialog.setGeo("GEOIP")
    dialog.setCategoryQuery("telegram")
    assert [c.name for c in rows(dialog, "categories")] == ["telegram"]
    dialog.toggleCategory("telegram")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert (rows(plan_rules, "rows")[0].name, rows(plan_rules, "rows")[0].caption) == (
        "GEOIP · telegram",
        "分类",
    )


def test_groups_change_type_and_hide(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_groups = routing.plan_groups()
    with qtbot.waitSignal(routing.applied):
        plan_groups.setType("region:JP", "select")
    japan = next(g for g in rows(plan_groups, "regions") if g.id == "region:JP")
    assert (japan.type, japan.caption) == ("select", "自动 · 手动选择 · 6 个节点")
    with qtbot.waitSignal(routing.applied):
        plan_groups.setHidden("region:JP", True)
    japan = next(g for g in rows(plan_groups, "regions") if g.id == "region:JP")
    assert japan.caption == "自动 · 已隐藏 · 手动选择 · 6 个节点"
    assert "日本" not in [o["label"] for o in prop(routing.plan_rules(), "targetOptions")]
    with qtbot.waitSignal(routing.applied):
        plan_groups.setType("auto", "fallback")
    assert rows(plan_groups, "builtin")[1].caption == "故障转移 · 27 个节点"


def test_plans_are_made_renamed_and_removed(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    first = prop(routing, "planId")
    assert not prop(routing, "canRemovePlan")
    with qtbot.waitSignal(routing.planSaved):
        routing.createPlan("游戏", first)
    assert prop(routing, "planName") == "游戏"
    assert [o["label"] for o in prop(routing, "planOptions")] == ["日常", "游戏"]
    with qtbot.waitSignal(routing.planSaved):
        routing.renamePlan("游戏加速")
    assert prop(routing, "planName") == "游戏加速"
    with qtbot.waitSignal(routing.planRemovalRequested):
        routing.requestRemovePlan()
    assert prop(routing, "planRemovalTitle") == "删除「游戏加速」？"  # noqa: RUF001
    assert prop(routing, "planRemovalText") == (
        "这是正在使用的方案，删除后改用「日常」。方案里的6 条规则会一起删除，无法恢复。"  # noqa: RUF001
    )
    with qtbot.waitSignal(routing.applied):
        routing.confirmRemovePlan()
    assert prop(routing, "planId") == first
    assert [o["label"] for o in prop(routing, "planOptions")] == ["日常"]


def test_the_pool_and_the_subscriptions_page(qtbot: QtBot, planned: ViewModels) -> None:
    profiles, routing = planned.profiles, planned.routing
    with qtbot.waitSignal(profiles.applied):
        profiles.setInPool("sample-b", False)
    qtbot.waitUntil(lambda: rows(profiles, "model")[1].note == "不加入节点池")
    assert not rows(profiles, "model")[1].inPool
    qtbot.waitUntil(lambda: prop(routing, "summary") == "使用「日常」 · 节点池 18 个节点")
    assert prop(profiles, "overrideScope") == "对“订阅自带”和“我的方案”都生效。"


def test_switching_back_uses_the_profiles_rules(qtbot: QtBot, planned: ViewModels) -> None:
    routing, host = planned.routing, planned.host
    assert isinstance(host, SimulatedHost)
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("profile")
    assert prop(routing, "mode") == "profile"
    assert host.config is not None
    assert host.config.composition is None
    qtbot.waitUntil(lambda: rows(planned.profiles, "model")[0].note == "规则使用中")
    assert not prop(planned.proxies, "showSource")
    assert planned.connections._entries == {}


def test_without_a_core_the_page_leaves_out_what_needs_one(
    qtbot: QtBot, planned: ViewModels
) -> None:
    """On macOS no core answers while the tunnel is down (D27): no rule test, no group
    preview, and a subscription imported meanwhile waits to be read at connect."""
    routing, profiles, overview, host = (
        planned.routing,
        planned.profiles,
        planned.overview,
        planned.host,
    )
    plan_rules = routing.plan_rules()
    assert isinstance(host, SimulatedHost)
    groups = routing._group_editor  # QML reads the property; Python has no converter
    assert prop(routing, "coreAvailable")
    assert prop(groups, "coreNote") == ""
    host.core_needs_tunnel = True
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(routing, "coreAvailable"))
    assert prop(groups, "coreNote") == "连接后显示"
    plan_rules.setTestText("example.com")
    plan_rules.runTest()
    assert prop(plan_rules, "testResult") == ""
    assert not prop(plan_rules, "testing")

    profiles.importUrl("https://new.example.com/sub", "新订阅", True)
    qtbot.waitUntil(lambda: len(rows(profiles, "model")) == 4)
    assert rows(profiles, "model")[3].note == "连接后读取"
    with qtbot.waitSignal(groups.changed):  # an open dialog drops its note
        overview.toggleConnection()
    qtbot.waitUntil(lambda: prop(routing, "coreAvailable"))
    assert prop(groups, "coreNote") == ""
    qtbot.waitUntil(lambda: rows(profiles, "model")[3].note == "")
    assert host.prepared == 1


def test_region_groups_fold_on_the_proxies_page(qtbot: QtBot, view_models: ViewModels) -> None:
    """With a plan, the region groups go into a section of their own, folded (§4.6)."""
    proxies = view_models.proxies
    proxies.refresh()
    qtbot.waitUntil(lambda: prop(proxies, "currentGroup") != "")
    proxies.set_pool({"香港 01": "示例订阅 A"}, frozenset({"故障转移", "视频服务"}))
    groups = rows(proxies, "groups")
    assert [(g.name, g.header, g.now) for g in groups][2] == ("地区", True, "2 个")
    assert [g.name for g in groups] == ["节点选择", "自动选择", "地区", "全球直连"]
    proxies.toggleRegions()
    groups = rows(proxies, "groups")
    assert [(g.name, g.region) for g in groups][2:5] == [
        ("地区", False),
        ("故障转移", True),
        ("视频服务", True),
    ]
    # The section of the group chosen stays open.
    proxies.selectGroup("故障转移")
    proxies.toggleRegions()
    assert "视频服务" in [g.name for g in rows(proxies, "groups")]
    assert rows(proxies, "nodes")[0].source == "示例订阅 A"


def test_an_info_pattern_the_core_refuses_stays_in_the_dialog(
    qtbot: QtBot, planned: ViewModels
) -> None:
    routing = planned.routing
    routing.saveInfoPattern("(")
    qtbot.waitUntil(lambda: prop(routing, "infoError") != "")
    assert prop(routing, "infoError").startswith("配置无效：")  # noqa: RUF001
    assert prop(routing, "infoPattern") == prop(routing, "defaultInfoPattern")
    with qtbot.waitSignal(routing.infoPatternSaved):
        routing.saveInfoPattern("香港")
    assert prop(routing, "infoPattern") == "香港"
