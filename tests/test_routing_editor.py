"""The routing page's editors (docs/routing.md §4.3, §4.5, §4.7, §5.5): the group editor,
the rule test, the search and the target filter, and plans in and out."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from solaris.models.routing import MODE_PLAN
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.routing_groups import GroupEditor
from solaris.viewmodels.routing_transfer import PlanTransfer
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

NOW = datetime(2026, 10, 29, 12).timestamp()


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def rows(view_model: QObject, name: str) -> list[Any]:
    return list(prop(view_model, name).rows)


def group_editor(routing: RoutingViewModel) -> GroupEditor:
    return routing._group_editor  # QML reads the property; Python has no converter for the type


def transfer(routing: RoutingViewModel) -> PlanTransfer:
    return routing._transfer


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
    routing = view_models.routing
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: prop(routing, "summary").endswith("27 个节点"))
    return view_models


def add_group(qtbot: QtBot, routing: RoutingViewModel, name: str) -> GroupEditor:
    """A custom group of Hong Kong nodes without 03, with DIRECT besides."""
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName(name)
    dialog.toggleRegion("HK")
    dialog.setExclude("keyword", "03")
    dialog.toggleOther("DIRECT")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    return dialog


def test_the_group_editor_previews_its_members(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    assert (prop(dialog, "title"), prop(dialog, "confirmLabel")) == ("新建策略组", "添加")
    assert not prop(dialog, "canSave")  # no name yet
    assert [(r.name, r.note) for r in rows(dialog, "regions")] == [
        ("香港", "9"),
        ("日本", "6"),
        ("新加坡", "6"),
        ("美国", "6"),
    ]
    assert [r.name for r in rows(dialog, "sources")] == [
        "全部节点",
        "示例订阅 A",
        "示例订阅 B",
        "本地配置",
    ]
    assert prop(dialog, "moreLabel") == "更多条件"
    qtbot.waitUntil(lambda: prop(dialog, "previewEmpty"))

    dialog.setName("香港精选")
    dialog.toggleRegion("HK")
    dialog.setExclude("keyword", "03")
    dialog.toggleOther("DIRECT")
    assert prop(dialog, "moreLabel") == "更多条件（已设置 2 项）"  # noqa: RUF001
    qtbot.waitUntil(lambda: prop(dialog, "previewTitle") == "6 个节点、1 个其他成员")
    preview = rows(dialog, "preview")
    assert [(r.name, r.source, r.other) for r in preview[:3]] == [
        ("直连", "", True),
        ("香港 01", "示例订阅 A", False),
        ("香港 02", "示例订阅 A", False),
    ]
    # The core measured some of them; the others were never tested.
    assert (preview[1].delay, preview[1].level) == ("68 ms", "good")
    assert preview[3].delay == "—"
    assert not prop(dialog, "previewEmpty")

    # A broken expression: the field says so and nothing can be saved.
    dialog.setNameMatch("regex", "(")
    assert prop(dialog, "nameInvalid")
    assert not prop(dialog, "canSave")
    dialog.setNameMatch("keyword", "")
    assert prop(dialog, "canSave")

    # The advanced settings are for groups that test delays.
    assert not prop(dialog, "hasAdvanced")
    dialog.setType("load-balance")
    assert prop(dialog, "hasAdvanced")
    dialog.setOption("interval", "600")
    dialog.setOption("strategy", "round-robin")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    plan = routing.plan()
    assert plan is not None
    group = plan.groups[-1]
    assert (group.kind, group.name, group.type) == ("custom", "香港精选", "load-balance")
    assert group.conditions == {
        "regions": ["HK"],
        "exclude": {"mode": "keyword", "pattern": "03"},
        "others": ["DIRECT"],
    }
    assert group.options == {"interval": 600, "strategy": "round-robin"}
    assert [(g.name, g.caption) for g in rows(routing.plan_groups(), "custom")] == [
        ("香港精选", "负载均衡 · 6 个节点、直连")
    ]
    # It is a target now, after the regions.
    assert "香港精选" in [o["label"] for o in prop(routing.plan_rules(), "targetOptions")]


def test_editing_and_copying_a_group(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_groups = routing.plan_groups()
    add_group(qtbot, routing, "香港精选")
    (row,) = rows(plan_groups, "custom")
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openEdit(row.id)
    assert (prop(dialog, "title"), prop(dialog, "confirmLabel")) == (
        "编辑策略组「香港精选」",
        "保存",
    )
    assert prop(dialog, "moreOpen")  # set conditions start open
    assert [r.id for r in rows(dialog, "regions") if r.picked] == ["HK"]
    # A group cannot hold itself, nor a group that holds it.
    assert row.id not in [r.id for r in rows(dialog, "others")]
    dialog.setName("香港")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    assert [g.name for g in rows(plan_groups, "custom")] == ["香港"]

    with qtbot.waitSignal(dialog.opened):
        dialog.openCopy(row.id)
    assert (prop(dialog, "title"), prop(dialog, "name")) == ("新建策略组", "香港 副本")
    dialog.toggleOther(row.id)  # the copy may hold the original
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    copy = rows(plan_groups, "custom")[1]
    with qtbot.waitSignal(dialog.opened):
        dialog.openEdit(row.id)
    looped = {r.id: r.added for r in rows(dialog, "others")}
    assert looped[copy.id]  # the copy holds the original, so it cannot join it

    # A region's copy starts from its region.
    with qtbot.waitSignal(dialog.opened):
        dialog.openCopy("region:JP")
    assert prop(dialog, "name") == "日本 副本"
    assert [r.id for r in rows(dialog, "regions") if r.picked] == ["JP"]


def test_single_nodes_beside_the_regions(qtbot: QtBot, planned: ViewModels) -> None:
    """Nodes are members beside the regions, picked from the pool by subscription or by
    region and found by their names, subscriptions or regions (routing.md §4.5)."""
    routing = planned.routing
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    assert prop(dialog, "nodeSummary") == "未挑选"
    assert not prop(dialog, "pickerOpen")
    nodes = rows(dialog, "nodes")
    assert len(nodes) == 27
    assert [(n.name, n.note, n.category) for n in nodes[8:10]] == [
        ("美国 02", "", "示例订阅 A"),
        ("香港 01 · 示例订阅 B", "", "示例订阅 B"),
    ]

    dialog.setGrouping("region")
    nodes = rows(dialog, "nodes")
    assert list(dict.fromkeys(n.category for n in nodes)) == ["香港", "日本", "新加坡", "美国"]
    assert [(n.name, n.note) for n in nodes[2:5]] == [
        ("香港 03", "示例订阅 A"),
        ("香港 01 · 示例订阅 B", "示例订阅 B"),
        ("香港 02 · 示例订阅 B", "示例订阅 B"),
    ]
    dialog.setNodeQuery("新加坡")
    assert [n.name for n in rows(dialog, "nodes")][:2] == ["新加坡 01", "新加坡 02"]
    dialog.setNodeQuery("本地配置")  # a subscription finds its nodes
    assert {n.note for n in rows(dialog, "nodes")} == {"本地配置"}
    dialog.setNodeQuery("火星")
    assert prop(dialog, "nodesEmpty")
    assert prop(dialog, "nodesEmptyText") == "没有匹配的节点"
    dialog.setNodeQuery("")

    dialog.setName("东京")
    picked = next(n for n in rows(dialog, "nodes") if n.name == "日本 02 · 示例订阅 B")
    dialog.toggleNode(picked.id)
    dialog.toggleOther("DIRECT")
    assert prop(dialog, "nodeSummary") == "已选 1 个：日本 02 · 示例订阅 B"  # noqa: RUF001
    assert prop(dialog, "nodeWarning") == ""
    assert prop(dialog, "moreLabel") == "更多条件（已设置 1 项）"  # noqa: RUF001
    qtbot.waitUntil(lambda: prop(dialog, "previewTitle") == "1 个节点、1 个其他成员")
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    plan = routing.plan()
    assert plan is not None
    # The node by its subscription and the name it has there, not the one the pool gave it.
    assert plan.groups[-1].conditions == {
        "nodes": [{"source": "sample-b", "name": "日本 02"}],
        "others": ["DIRECT"],
    }


def test_picked_nodes_the_pool_lost_are_marked(qtbot: QtBot, planned: ViewModels) -> None:
    """A picked node the pool no longer has stays in the group, marked with the reason, until
    it is taken out; the core leaves it out meanwhile (routing.md §5.2)."""
    routing, profiles = planned.routing, planned.profiles
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName("挑选")
    for key in (
        "sample-a\n香港 01",
        "sample-a\n香港 09",  # an update took it away
        "sample-gone\n日本 01",  # its subscription was deleted
        "sample-c\n美国 01",  # its subscription leaves the pool below
    ):
        dialog.toggleNode(key)
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    with qtbot.waitSignal(profiles.applied):
        profiles.setInPool("sample-c", False)
    qtbot.waitUntil(lambda: prop(routing, "summary") == "使用「日常」 · 节点池 18 个节点")
    (row,) = rows(routing.plan_groups(), "custom")
    assert (row.caption, row.warning) == ("手动选择 · 1 个节点", "3 个挑选的节点已失效")

    with qtbot.waitSignal(dialog.opened):
        dialog.openEdit(row.id)
    assert prop(dialog, "nodeSummary") == "已选 4 个：香港 01、香港 09、日本 01、美国 01"  # noqa: RUF001
    assert prop(dialog, "nodeWarning") == "3 个已失效：香港 09、日本 01、美国 01"  # noqa: RUF001
    stale = [(n.name, n.note, n.category, n.picked) for n in rows(dialog, "nodes")[:4]]
    assert stale == [
        ("香港 09", "订阅里已没有这个节点", "已失效", True),
        ("日本 01", "订阅已删除", "已失效", True),
        ("美国 01", "订阅不在节点池", "已失效", True),
        ("香港 01", "", "示例订阅 A", True),
    ]
    dialog.toggleNode("sample-a\n香港 09")  # unchecked, it goes
    stale = [(n.name, n.category) for n in rows(dialog, "nodes")[:2]]
    assert stale == [("日本 01", "已失效"), ("美国 01", "已失效")]
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    (row,) = rows(routing.plan_groups(), "custom")
    assert row.warning == "2 个挑选的节点已失效"


def test_removing_a_group_names_its_references(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    plan_groups = routing.plan_groups()
    add_group(qtbot, routing, "香港精选")
    (group,) = rows(plan_groups, "custom")
    openai = rows(plan_rules, "rows")[2]
    with qtbot.waitSignal(routing.applied):
        plan_rules.setFallback(group.id)
    dialog = group_editor(routing)
    with qtbot.waitSignal(dialog.opened):
        dialog.openAdd()
    dialog.setName("流媒体")
    dialog.toggleOther(group.id)
    with qtbot.waitSignal(dialog.saved):
        dialog.save()
    plan = routing.plan()
    assert plan is not None
    rule = next(r for r in plan.rules if r.id == openai.id)
    with qtbot.waitSignal(routing.applied):
        routing.commit(
            replace(
                plan,
                rules=tuple(replace(r, target=group.id) if r is rule else r for r in plan.rules),
            )
        )

    with qtbot.waitSignal(plan_groups.removalRequested):
        plan_groups.requestRemove(group.id)
    assert prop(plan_groups, "removalTitle") == "删除策略组「香港精选」？"  # noqa: RUF001
    assert prop(plan_groups, "removalText") == (
        "规则「OpenAI」「其他流量」以它为目标，删除后改走「节点选择」。策略组「流媒体」里不再包含它。"  # noqa: RUF001
    )
    with qtbot.waitSignal(routing.applied):
        plan_groups.confirmRemove()
    assert [g.name for g in rows(plan_groups, "custom")] == ["流媒体"]
    assert next(r for r in rows(plan_rules, "rows") if r.id == openai.id).target == "节点选择"
    assert prop(plan_rules, "fallbackLabel") == "节点选择"
    assert prop(routing, "undoText") == "已删除「香港精选」"
    plan = routing.plan()
    assert plan is not None
    assert "others" not in plan.groups[-1].conditions
    with qtbot.waitSignal(routing.applied):
        routing.undo()
    assert [g.name for g in rows(plan_groups, "custom")] == ["香港精选", "流媒体"]
    assert prop(plan_rules, "fallbackLabel") == "香港精选"


def test_the_rule_test_names_the_entry_and_the_chain(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    plan_rules._test_timer.setInterval(10)
    plan_rules.setTestText("https://chat.openai.com/c/1")
    qtbot.waitUntil(lambda: prop(plan_rules, "testResult") != "")
    # The simulated core runs its sample groups, not the plan's region groups.
    assert prop(plan_rules, "testResult") == "命中「OpenAI」 → 🇺🇸 美国"
    hits = [r.name for r in rows(plan_rules, "rows") if r.hit]
    assert hits == ["OpenAI"]
    assert not prop(plan_rules, "fallbackHit")

    plan_rules.setTestText("example.org")
    qtbot.waitUntil(lambda: prop(plan_rules, "fallbackHit"))
    assert prop(plan_rules, "testResult") == "命中「其他流量」 → 节点选择 → 香港 01"
    assert not [r for r in rows(plan_rules, "rows") if r.hit]

    plan_rules.setTestText("not a host!")
    qtbot.waitUntil(lambda: prop(plan_rules, "testFailed"))
    assert prop(plan_rules, "testResult") == "「not a host!」不是域名或 IP"
    plan_rules.setTestText("")
    assert prop(plan_rules, "testResult") == ""

    # A change to the plan tests the text again.
    plan_rules.setTestText("10.1.2.3")
    qtbot.waitUntil(lambda: prop(plan_rules, "testResult").startswith("命中「私有网络」"))
    with qtbot.waitSignal(routing.applied):
        plan_rules.setRuleEnabled(rows(plan_rules, "rows")[1].id, False)
    qtbot.waitUntil(lambda: prop(plan_rules, "testResult").startswith("命中「其他流量」"))


def test_search_and_target_filter(qtbot: QtBot, planned: ViewModels) -> None:
    routing = planned.routing
    plan_rules = routing.plan_rules()
    assert not prop(plan_rules, "filtering")
    labels = [
        (o["label"], o.get("separator", False)) for o in prop(plan_rules, "targetFilterOptions")
    ]
    assert labels == [("全部目标", False), ("美国", True), ("直连", False), ("拒绝", False)]
    plan_rules.setTargetFilter("DIRECT")
    assert prop(plan_rules, "filtering")
    assert prop(plan_rules, "targetFilterLabel") == "直连"
    assert [r.name for r in rows(plan_rules, "rows")] == ["私有网络", "国内常用"]
    # Moves still go by the whole list.
    assert [(r.first, r.last) for r in rows(plan_rules, "rows")] == [(False, False), (False, True)]
    plan_rules.setQuery("GEOIP")
    assert [r.name for r in rows(plan_rules, "rows")] == ["私有网络", "国内常用"]
    plan_rules.setQuery("私有")
    assert [r.name for r in rows(plan_rules, "rows")] == ["私有网络"]
    plan_rules.setQuery("nothing")
    assert prop(plan_rules, "filteredEmpty")
    plan_rules.setQuery("")
    plan_rules.setTargetFilter("")
    assert len(rows(plan_rules, "rows")) == 6
    assert not prop(plan_rules, "filteredEmpty")


def test_a_profiles_rules_become_a_plan(qtbot: QtBot, view_models: ViewModels) -> None:
    routing = view_models.routing
    qtbot.waitUntil(lambda: prop(view_models.overview, "connected"))
    plans_before = len(prop(routing, "planOptions"))
    moving = transfer(routing)
    with qtbot.waitSignal(moving.conversionReady):
        moving.convertProfile()
    assert prop(moving, "conversionTitle") == "以「示例订阅 A」创建方案"
    assert prop(moving, "conversionSummary") == ("1 个策略组（其中 1 个并入内置组）、1 条规则。")  # noqa: RUF001
    assert prop(moving, "suggestedName") == "示例订阅 A"
    with qtbot.waitSignal(moving.converted):
        moving.create("A 的规则", True)
    assert prop(routing, "mode") == MODE_PLAN
    assert prop(routing, "planName") == "A 的规则"
    assert len(prop(routing, "planOptions")) == plans_before + 1


def test_imports_say_what_stays_out(qtbot: QtBot, planned: ViewModels, tmp_path: Path) -> None:
    routing = planned.routing
    source = tmp_path / "rules.yaml"
    source.write_text(
        "proxies:\n  - {name: 香港 01, type: http, server: a, port: 1}\n"
        "proxy-groups:\n  - {name: 媒体, type: select, proxies: [DIRECT]}\n"
        "rules:\n  - DOMAIN-SUFFIX,a.example,媒体\n  - DOMAIN,b.example,媒体\n"
        "  - RULE-SET,ads,REJECT\n  - PROCESS-NAME,ssh,DIRECT\n  - MATCH,媒体\n",
        encoding="utf-8",
    )
    moving = transfer(routing)
    with qtbot.waitSignal(moving.conversionReady):
        moving.importFile(QUrl.fromLocalFile(str(source)).toString())
    assert prop(moving, "conversionTitle") == "以「rules」创建方案"
    assert prop(moving, "conversionSummary") == (
        "1 个策略组、5 条规则，其中 1 条保留为原始规则。"  # noqa: RUF001
        "方案里有 2 条规则条目（相邻的同类规则合为一条）。"  # noqa: RUF001
        "1 条无法转换，不会带入方案：规则集 1 条。"  # noqa: RUF001
    )
    assert prop(moving, "skippedRules") == ["RULE-SET,ads,REJECT"]
    current = prop(routing, "planName")
    with qtbot.waitSignal(moving.converted):
        moving.create("导入的规则", False)
    assert prop(routing, "planName") == current  # not in use
    assert "导入的规则" in [o["label"] for o in prop(routing, "planOptions")]


def test_export_and_the_generated_configuration(
    qtbot: QtBot, planned: ViewModels, tmp_path: Path
) -> None:
    routing = planned.routing
    moving = transfer(routing)
    with qtbot.waitSignal(moving.yamlReady):
        moving.exportPlan()
    assert prop(moving, "yamlTitle") == "导出「日常」"
    assert prop(moving, "sharing")
    shared = yaml.safe_load(prop(moving, "yaml"))
    assert list(shared) == ["proxy-groups", "rules"]
    moving.copyYaml()
    assert QGuiApplication.clipboard().text() == prop(moving, "yaml")
    assert prop(moving, "notice") == "已复制"
    target = tmp_path / "out.yaml"
    moving.saveYaml(QUrl.fromLocalFile(str(target)).toString())
    assert target.read_text(encoding="utf-8") == prop(moving, "yaml")
    assert prop(moving, "notice") == "已保存到 out.yaml"

    with qtbot.waitSignal(moving.yamlReady):
        moving.showGenerated()
    assert prop(moving, "yamlTitle") == "生成的配置"
    assert not prop(moving, "sharing")
    assert "proxies:" in prop(moving, "yaml")  # the whole configuration, not the plan alone
