"""Routing through the UI on the development service. A subscription with only nodes routes
with the default plan; a rule added from the routing page sends a domain to a region's node
and the connections page names it; switching to a subscription's own rules routes as that
subscription says.

The pool's nodes are the local HTTP proxies of tests/e2e/servers.py, and the core reads the
GEO data the installers ship (scripts/fetch_geodata.py, cached in build/geodata).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from scripts.fetch_geodata import seed
from solaris.models.library import Routing
from solaris.models.routing import PlanGroup
from tests.e2e.app import App, nodes_profile
from tests.e2e.servers import ProxyNode, WebServer
from tests.e2e.test_routing import nodes_only

pytestmark = pytest.mark.e2e


def tunnels(node: ProxyNode, server: WebServer) -> int:
    """How many tunnels the node opened to the server."""
    return sum(target.endswith(f":{server.port}") for target in node.targets)


def local_delay_tests(app: App, health: WebServer) -> None:
    """Point the plan's automatic groups at a local server for their delay tests, so that
    nothing leaves the machine. The plan is written directly, not through the routing page."""
    library, runner = app.view_models.library, app.view_models.runner
    routing = library.routing_snapshot()
    plan = routing.plan()
    assert plan is not None
    options = {"url": health.url("/generate_204")}
    groups = [replace(g, options=options) if g.id == "auto" else g for g in plan.groups]
    groups += [
        PlanGroup(f"region:{code}", "region", "url-test", options=options) for code in ("HK", "JP")
    ]
    done: list[Routing] = []
    runner.submit(library.plans.put_plan(replace(plan, groups=tuple(groups))), done.append)
    app.until(lambda: done)
    app.view_models.routing.reload()


def rule_names(app: App) -> dict[str, Any]:
    """The connections page's rows by host, without the port."""
    return {row.host.rpartition(":")[0]: row for row in app.rows(app.view_models.connections)}


def test_my_plan_then_the_subscriptions_rules(app: App) -> None:
    vm, network = app.view_models, app.network
    routing, editor = vm.routing, vm.routing._editor
    seed(app.service.home)  # the template's GEOSITE and GEOIP rules need the data

    # 1. A subscription with only nodes: no rules of its own, so the default plan.
    app.import_file(app.write("airport.yaml", nodes_only(network)))
    app.until(lambda: app.prop(routing, "mode") == "plan")
    app.until(lambda: app.prop(routing, "planName") == "日常")
    (airport,) = app.rows(vm.profiles)
    assert airport.hasRules is False
    assert app.prop(vm.profiles, "activeName") == ""
    with WebServer("health") as health:
        local_delay_tests(app, health)

        # 2. Connect: the core composes the pool and the plan.
        app.connect()
        vm.shell.setPage("routing")
        app.until(lambda: app.prop(routing, "summary") == "使用「日常」 · 节点池 2 个节点")
        rules = [(r.name, r.target, r.warning) for r in app.rows_of(routing, "rules")]
        assert rules[:3] == [
            ("广告", "拒绝", ""),
            ("私有网络", "直连", ""),
            ("OpenAI", "美国", "无节点，暂走节点选择"),  # noqa: RUF001
        ]
        assert [o["label"] for o in app.prop(routing, "targetOptions")][2:4] == ["香港", "日本"]
        app.screenshot("routing-plan")
        # The proxies page names each node's subscription and folds the region groups.
        vm.shell.setPage("proxies")
        app.until(lambda: app.prop(vm.proxies, "showSource"))
        app.until(lambda: len(app.rows_of(vm.proxies, "groups")) == 4)
        groups = [(g.name, g.header) for g in app.rows_of(vm.proxies, "groups")]
        assert groups == [
            ("节点选择", False),
            ("自动选择", False),
            ("地区", True),
            ("GLOBAL", False),
        ]
        vm.proxies.toggleRegions()
        vm.proxies.selectGroup("🇯🇵 日本")
        app.until(lambda: [n.source for n in app.rows_of(vm.proxies, "nodes")] == ["airport"])
        app.screenshot("proxies-plan")
        vm.shell.setPage("routing")

        # 3. A rule from the add-rule dialog: localhost to Japan, first in the list.
        editor.openAdd()
        editor.setKind("domain")
        editor.setText("=localhost")
        editor.setTarget("region:JP")
        assert app.prop(editor, "canSave")
        editor.save()
        app.until(lambda: app.rows_of(routing, "rules")[0].name == "自定义域名（1 个）")  # noqa: RUF001
        assert app.rows_of(routing, "rules")[0].target == "日本"

        # 4. A new connection goes to Japan's node, and the connections page names the entry.
        vm.shell.setPage("connections")
        node_b_before = tunnels(network.node_b, network.proxied)
        url = f"http://localhost:{network.proxied.port}/slow?seconds=4"
        slow = app.stream_url(url)
        app.until(lambda: "localhost" in rule_names(app))
        row = rule_names(app)["localhost"]
        assert (row.ruleName, row.rule) == ("自定义域名（1 个）", "DOMAIN,localhost")  # noqa: RUF001
        assert row.chain.startswith("🇯🇵 日本")
        app.screenshot("connections-entry")
        assert slow.done.wait(10)
        assert slow.error == ""
        assert tunnels(network.node_b, network.proxied) == node_b_before + 1

    # 5. A subscription with rules: the mode stays, then 订阅自带 routes as it says, which
    #    is through node-a for the proxied server.
    app.import_file(app.write("nodes.yaml", nodes_profile(network)))
    app.until(lambda: app.prop(vm.profiles, "activeName") == "nodes")
    assert app.prop(routing, "mode") == "plan"
    vm.shell.setPage("routing")
    routing.setMode("profile")
    app.until(lambda: app.prop(routing, "summary") == "使用「nodes」自带的规则")
    app.until(lambda: app.prop(routing, "profileRunning"))
    assert [(r.match, r.target) for r in app.rows_of(routing, "profileRules")] == [
        (f"DST-PORT,{network.proxied.port}", "节点选择"),
        ("MATCH", "DIRECT"),
    ]
    app.screenshot("routing-profile")
    node_a_before = tunnels(network.node_a, network.proxied)
    assert app.curl(network.proxied.url()) == "proxied"
    assert tunnels(network.node_a, network.proxied) == node_a_before + 1
    vm.shell.setPage("profiles")
    app.until(lambda: app.rows(vm.profiles)[1].note == "规则使用中")
    app.screenshot("profiles-rules")
    assert app.warnings == []
