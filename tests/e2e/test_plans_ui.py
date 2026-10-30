"""Roadmap M8b: the acceptance of docs/routing.md §10 through the UI on the development
service. A subscription of 400 rules becomes a plan whose summary is accurate; a group that
leaves out the 0.1x nodes previews the members the proxies page shows once connected; and
the rule test names, for 20 sample hosts, the rule actual connections match.

The pool's nodes are the local HTTP proxies of tests/e2e/servers.py, and the core reads the
GEO data the installers ship (scripts/fetch_geodata.py, cached in build/geodata).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from scripts.fetch_geodata import seed
from solaris.models import plans
from solaris.models.library import Routing
from solaris.models.routing import Plan, PlanGroup, PlanRule
from tests.e2e.app import App, Network
from tests.e2e.servers import WebServer

pytestmark = pytest.mark.e2e

# Categories the shipped GEO data has; the conversion leaves out any it lacks.
CATEGORIES = ("cn", "google", "telegram", "youtube", "netflix", "github", "apple", "private")


def subscription_of_400_rules(network: Network) -> tuple[str, list[str]]:
    """A subscription as airports serve them: nodes, a few groups and 400 rules of every
    kind the conversion treats differently. Returns it with its rule lines."""
    targets = ("节点选择", "流媒体", "DIRECT", "广告拦截", "香港节点")
    lines = []
    for n in range(399):
        target = targets[n // 9 % len(targets)]
        match n % 10:
            case 0 | 1 | 2:
                line = f"DOMAIN-SUFFIX,site{n}.example,{target}"
            case 3:
                line = f"DOMAIN-KEYWORD,word{n},{target}"
            case 4:
                line = f"IP-CIDR,10.{n % 256}.0.0/16,{target},no-resolve"
            case 5:
                line = f"GEOSITE,{CATEGORIES[n % len(CATEGORIES)]},{target}"
            case 6:
                line = f"DST-PORT,{1000 + n},{target}"
            case 7:
                line = f"RULE-SET,set{n},{target}"
            case 8:
                line = f"PROCESS-NAME,app{n},{target}"
            case _:
                line = f"DOMAIN,host{n}.example,{target}"
        lines.append(line)
    lines.append("MATCH,节点选择")
    a, b = network.node_a.port, network.node_b.port
    text = f"""\
proxies:
  - {{name: 🇭🇰 香港 01, type: http, server: 127.0.0.1, port: {a}}}
  - {{name: 🇭🇰 香港 02, type: http, server: 127.0.0.1, port: {b}}}
  - {{name: 🇯🇵 日本 01, type: http, server: 127.0.0.1, port: {b}}}
proxy-groups:
  - {{name: 节点选择, type: select, proxies: [香港节点, 🇯🇵 日本 01, DIRECT]}}
  - {{name: 香港节点, type: url-test, proxies: [🇭🇰 香港 01, 🇭🇰 香港 02]}}
  - {{name: 流媒体, type: select, proxies: [节点选择, 香港节点]}}
  - {{name: 广告拦截, type: select, proxies: [REJECT, DIRECT]}}
"""
    # The rule sets the subscription's own configuration needs; a plan has no place for them.
    sets = [line.split(",")[1] for line in lines if line.startswith("RULE-SET,")]
    text += "rule-providers:\n" + "".join(
        f"  {name}: {{type: inline, behavior: domain, payload: ['+.{name}.example']}}\n"
        for name in sets
    )
    text += "rules:\n" + "".join(f"  - {line}\n" for line in lines)
    return text, lines


def test_converting_a_subscription_of_400_rules(app: App) -> None:
    vm, network = app.view_models, app.network
    routing, transfer = vm.routing, vm.routing._transfer
    seed(app.service.home)
    text, lines = subscription_of_400_rules(network)

    # A subscription with rules: its own rules route (routing.md §5.1).
    app.import_file(app.write("airport.yaml", text))
    app.until(lambda: app.prop(routing, "mode") == "profile")
    vm.shell.setPage("routing")
    transfer.convertProfile()
    app.until(lambda: app.prop(transfer, "conversionTitle") == "以「airport」创建方案")
    # 4 groups, 节点选择 merged; 400 rules: 40 RULE-SET left out, 80 DST-PORT and
    # PROCESS-NAME kept raw; neighbouring domains and ranges of one target share an entry.
    summary = app.prop(transfer, "conversionSummary")
    assert summary.startswith(
        "4 个策略组（其中 1 个并入内置组）、400 条规则，其中 80 条保留为原始规则。"  # noqa: RUF001
    ), summary
    assert summary.endswith("40 条无法转换，不会带入方案：规则集 40 条。"), summary  # noqa: RUF001
    entries = int(re.search(r"方案里有 (\d+) 条规则条目", summary).group(1))  # type: ignore[union-attr]
    assert len(app.prop(transfer, "skippedRules")) == 40
    app.screenshot("routing-convert")

    with WebServer("health"):
        transfer.create("airport 的规则", True)
        app.until(lambda: app.prop(routing, "mode") == "plan")
        app.until(lambda: app.prop(routing, "planName") == "airport 的规则")
        assert len(app.rows_of(routing, "rules")) == entries
        app.connect()
        # The core runs every converted rule, in order and to the same targets.
        groups = {g.id: g for g in routing.plan().groups}  # type: ignore[union-attr]
        hong_kong = next(g for g in groups.values() if g.name == "香港节点")
        assert hong_kong.conditions == {"regions": ["HK"]}

        def running() -> list[str]:
            routing_now: list[Routing] = []
            vm.runner.submit(vm.library.routing(), routing_now.append)
            app.until(lambda: routing_now)
            report = routing_now[0].report
            assert report is not None
            return [",".join(p for p in (r.type, r.payload, r.proxy) if p) for r in report.rules]

        expected = []
        for line in lines:
            kind, *rest = line.split(",")
            if kind == "RULE-SET":
                continue
            if kind == "MATCH":
                expected.append("MATCH,节点选择")
                continue
            payload, target = rest[0], rest[1]
            expected.append(f"{kind},{payload},{target}")
        assert running() == expected
    vm.shell.setPage("routing")
    app.screenshot("routing-converted")
    assert app.warnings == []


def low_rate_nodes(network: Network) -> str:
    a, b = network.node_a.port, network.node_b.port
    return f"""\
proxies:
  - {{name: 🇭🇰 香港 01, type: http, server: 127.0.0.1, port: {a}}}
  - {{name: 🇭🇰 香港 02 0.1x, type: http, server: 127.0.0.1, port: {b}}}
  - {{name: 🇯🇵 日本 01, type: http, server: 127.0.0.1, port: {b}}}
  - {{name: 🇯🇵 日本 02 0.1倍率, type: http, server: 127.0.0.1, port: {a}}}
  - {{name: 🇺🇸 美国 01, type: http, server: 127.0.0.1, port: {a}}}
  - {{name: 剩余流量 12 GB, type: http, server: 127.0.0.1, port: 9}}
"""


def test_the_preview_is_what_the_proxies_page_shows(app: App) -> None:
    vm = app.view_models
    routing, editor = vm.routing, vm.routing._group_editor
    seed(app.service.home)
    app.import_file(app.write("airport.yaml", low_rate_nodes(app.network)))
    app.until(lambda: app.prop(routing, "mode") == "plan")
    vm.shell.setPage("routing")
    app.until(lambda: app.prop(routing, "summary") == "使用「日常」 · 节点池 5 个节点")

    # A group of every node but the 0.1x ones.
    editor.openAdd()
    editor.setName("常规倍率")
    editor.setType("url-test")
    editor.toggleSource("*")
    editor.setExclude("keyword", "0.1")
    app.until(lambda: app.prop(editor, "previewTitle") == "3 个节点")
    previewed = [row.name for row in app.rows_of(editor, "preview")]
    assert previewed == ["🇭🇰 香港 01", "🇯🇵 日本 01", "🇺🇸 美国 01"]
    app.screenshot("routing-group-editor")
    with WebServer("health") as health:
        editor.setAdvancedOpen(True)
        editor.setOption("url", health.url("/generate_204"))
        editor.save()
        app.until(lambda: [g.name for g in app.rows_of(routing, "customGroups")] == ["常规倍率"])
        local_tests(app, health)
        app.connect()

        # The proxies page lists the same members.
        vm.shell.setPage("proxies")
        app.until(lambda: "常规倍率" in [g.name for g in app.rows_of(vm.proxies, "groups")])
        vm.proxies.selectGroup("常规倍率")
        app.until(lambda: [n.name for n in app.rows_of(vm.proxies, "nodes")] == previewed)
        app.screenshot("proxies-custom-group")
    assert app.warnings == []


def local_tests(app: App, health: WebServer) -> None:
    """Point the built-in and region groups' delay tests at a local server."""
    library, runner = app.view_models.library, app.view_models.runner
    plan = library.routing_snapshot().plan()
    assert plan is not None
    options = {"url": health.url("/generate_204")}
    groups = [replace(g, options=options) if g.id == plans.AUTO else g for g in plan.groups]
    groups += [
        PlanGroup(f"region:{code}", "region", "url-test", options=options)
        for code in ("HK", "JP", "US")
    ]
    done: list[Routing] = []
    runner.submit(library.put_plan(replace(plan, groups=tuple(groups))), done.append)
    app.until(lambda: done)
    app.view_models.routing.reload()


def sample_plan(plan: Plan, source: str, health: WebServer) -> Plan:
    """Rules of every kind the test samples, with targets that never leave the machine:
    REJECT for the internet's domains, nodes and DIRECT for local ones."""
    options = {"url": health.url("/generate_204")}

    def rule(rule_id: str, kind: str, content: dict[str, object], target: str) -> PlanRule:
        return PlanRule(rule_id, kind, content, target)

    groups = [
        *(replace(g, options=options) if g.id == plans.AUTO else g for g in plan.groups),
        PlanGroup("region:HK", "region", "url-test", options=options),
        PlanGroup("region:JP", "region", "url-test", options=options),
        PlanGroup(
            "mine",
            "custom",
            "select",
            "本机",
            {"nodes": [{"source": source, "name": "🇭🇰 香港 01"}]},
        ),
    ]
    rules = (
        rule("a", "domain", {"items": ["a.test"]}, "DIRECT"),
        rule("b", "domain", {"items": ["=b.test"]}, "region:JP"),
        rule("kw", "domain", {"items": ["~tracker"]}, "REJECT"),
        rule("tg", "category", {"type": "GEOSITE", "value": "telegram"}, "REJECT"),
        rule("cn", "category", {"type": "GEOSITE", "value": "cn"}, "REJECT"),
        rule("local", "domain", {"items": ["=localhost"]}, "region:HK"),
        rule("lan", "ip", {"items": ["10.0.0.0/8"], "no_resolve": True}, "REJECT"),
        rule("loop", "ip", {"items": ["127.0.0.0/8"], "no_resolve": True}, "mine"),
    )
    return replace(plan, groups=tuple(groups), rules=rules, fallback="REJECT")


SAMPLES = (
    "a.test",
    "www.a.test",
    "deep.sub.a.test",
    "b.test",
    "x.b.test",  # not b.test itself: the hosts table makes it 127.0.0.1, so loopback
    "my-tracker.test",
    "tracker.example.org",
    "t.me",
    "telegram.org",
    "api.telegram.org",
    "baidu.com",
    "qq.com",
    "localhost",
    "127.0.0.1",
    "10.1.2.3",
    "10.255.0.1",
    "other.test",
    "example.org",
    "unknown.example",
    "z.a.test",
)

# What mihomo's log calls the rule types of the samples.
LOG_TYPES = {
    "DOMAIN": "Domain",
    "DOMAIN-SUFFIX": "DomainSuffix",
    "DOMAIN-KEYWORD": "DomainKeyword",
    "GEOSITE": "GeoSite",
    "IP-CIDR": "IPCIDR",
    "MATCH": "Match",
}
# The service logs mihomo's lines in logfmt: msg="[TCP] ... using 节点选择[node]".
MATCHED = re.compile(
    r"--> (?P<to>\S+) match (?P<type>\w+)(?:\((?P<payload>[^)]*)\))? using (?P<using>[^\"]+)"
)
FAILED = re.compile(
    r"dial (?P<using>.+?) \(match (?P<type>\w+)/(?P<payload>[^)]*)\) \S+ --> (?P<to>\S+) error"
)


def logged_match(app: App, host: str, port: int) -> tuple[str, str, str]:
    """The rule type, payload and outermost target mihomo logged for a connection."""
    destination = f"{host}:{port}"
    for line in reversed(app.service.log.read_text(errors="replace").splitlines()):
        for pattern in (MATCHED, FAILED):
            found = pattern.search(line)
            if found and found["to"] == destination:
                return found["type"], found["payload"] or "", found["using"].split("[")[0]
    raise AssertionError(f"no connection to {destination} in the service log")


def test_the_rule_test_agrees_with_connections(app: App) -> None:
    vm, network = app.view_models, app.network
    routing = vm.routing
    seed(app.service.home)
    app.import_file(app.write("airport.yaml", low_rate_nodes(network)))
    app.until(lambda: app.prop(routing, "mode") == "plan")
    source = app.profile_id("airport")
    library, runner = vm.library, vm.runner
    # The .test domains resolve to this machine, so DIRECT reaches the local server.
    saved: list[object] = []
    override = "hosts:\n  '+.test': 127.0.0.1\n"
    runner.submit(library.save_override(None, override), saved.append)
    app.until(lambda: saved)
    with WebServer("health") as health, WebServer("sample") as sample:
        plan = library.routing_snapshot().plan()
        assert plan is not None
        done: list[Routing] = []
        runner.submit(library.put_plan(sample_plan(plan, source, health)), done.append)
        app.until(lambda: done)
        routing.reload()
        app.connect()
        vm.shell.setPage("routing")

        tested = {}
        routing._test_timer.setInterval(0)
        for host in SAMPLES:
            routing.setTestText("")  # clears the last result
            routing.setTestText(f"{host}:{sample.port}")
            app.until(lambda: routing._test is not None and not app.prop(routing, "testing"))
            tested[host] = routing._test
            if host == "t.me":
                app.screenshot("routing-rule-test")

        for host in SAMPLES:
            app.curl(f"http://{host}:{sample.port}/")
            kind, payload, target = logged_match(app, host, sample.port)
            result = tested[host]
            assert result is not None
            assert result.rule is not None, host
            assert LOG_TYPES[result.rule.type] == kind, host
            assert result.rule.payload == payload, host
            assert result.chain[0] == target, host
        names = {
            host: result.rule.entry_id for host, result in tested.items() if result and result.rule
        }
        assert names == {
            "a.test": "a",
            "www.a.test": "a",
            "deep.sub.a.test": "a",
            "b.test": "b",
            "x.b.test": "loop",
            "my-tracker.test": "kw",
            "tracker.example.org": "kw",
            "t.me": "tg",
            "telegram.org": "tg",
            "api.telegram.org": "tg",
            "baidu.com": "cn",
            "qq.com": "cn",
            "localhost": "local",
            "127.0.0.1": "loop",
            "10.1.2.3": "lan",
            "10.255.0.1": "lan",
            "other.test": "loop",
            "example.org": "fallback",
            "unknown.example": "fallback",
            "z.a.test": "a",
        }
    assert json.dumps(app.warnings) == "[]"
