"""PreviewGroup, MatchRule, ConvertToPlan and ExportPlan over the protocol, and the simulated
host's rule test and export (docs/routing.md §8.2, §8.3). Its previews and conversions are
checked against the core in test_simulated.py."""

import asyncio
import json

import pytest
import yaml

from solaris.host.base import (
    EXPORT_GENERATED,
    Composition,
    CompositionSource,
    GroupPreview,
    HostError,
    PlanConversion,
    PoolNode,
    Rule,
    RuleMatch,
    RunOptions,
    SkippedRule,
    TunnelConfig,
)
from solaris.host.service import ServiceHost
from solaris.protocol import control_pb2
from tests.support.simulated import SAMPLE_CONFIG, SimulatedHost
from tests.test_control_client import serve

pb = control_pb2

PLAN = {
    "version": 1,
    "groups": [
        {
            "id": "cheap",
            "kind": "custom",
            "name": "低倍率",
            "members": {
                "regions": ["HK"],
                "exclude": {"mode": "keyword", "pattern": "03"},
                "nodes": [{"source": "sub-a", "name": "日本 01"}],
                "others": ["DIRECT", "region:*"],
            },
        }
    ],
    "rules": [
        {
            "id": "tg",
            "kind": "service",
            "items": [{"type": "GEOSITE", "value": "telegram"}],
            "target": "cheap",
        },
        {"id": "lan", "items": [{"type": "IP-CIDR", "value": "10.0.0.0/8"}], "target": "DIRECT"},
    ],
    "fallback": "proxy",
}
COMPOSITION = Composition(
    (CompositionSource("sub-a", "示例订阅 A", SAMPLE_CONFIG),), json.dumps(PLAN, ensure_ascii=False)
)


def test_service_host_plan_methods() -> None:
    received: list[pb.Request] = []
    node = pb.PoolNode(name="香港 01", original_name="香港 01", source_id="sub-a", region="HK")

    async def answer(request: pb.Request) -> pb.Response:
        received.append(request)
        match request.WhichOneof("method"):
            case "preview_group":
                return pb.Response(
                    preview_group=pb.PreviewGroupResponse(nodes=[node], others=["DIRECT"])
                )
            case "match_rule":
                rule = pb.Rule(type="GEOSITE", payload="telegram", proxy="低倍率", entry_id="tg")
                matched = pb.MatchRuleResponse(
                    matched=True, index=1, rule=rule, chain=["香港 01", "低倍率"]
                )
                return pb.Response(match_rule=matched)
            case "convert_to_plan":
                converted = pb.ConvertToPlanResponse(
                    plan_json="{}",
                    group_count=2,
                    merged_group_count=1,
                    rule_count=3,
                    raw_rule_count=1,
                    entry_count=2,
                    skipped=[pb.SkippedRule(rule="RULE-SET,ads,REJECT", reason="rule_set")],
                )
                return pb.Response(convert_to_plan=converted)
            case _:
                return pb.Response(export_plan=pb.ExportPlanResponse(yaml="rules: []\n"))

    async def scenario() -> tuple[GroupPreview, RuleMatch, RuleMatch, PlanConversion, str]:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                config = TunnelConfig(overrides=("mode: rule\n",), composition=COMPOSITION)
                return (
                    await host.preview_group(COMPOSITION, "cheap"),
                    await host.match_rule("t.me", 80, "udp", config),
                    await host.match_rule("t.me"),
                    await host.convert_to_plan("rules: []\n", source_id="sub-a", locale="en"),
                    await host.export_plan(
                        TunnelConfig(composition=COMPOSITION, options=RunOptions(mixed_port=1)),
                        EXPORT_GENERATED,
                    ),
                )
            finally:
                await host.close()

    preview, drafted, running, converted, exported = asyncio.run(scenario())
    assert preview == GroupPreview(
        (PoolNode("香港 01", "香港 01", "sub-a", "", "HK"),), ("DIRECT",)
    )
    # The chain reads from the rule's target down to the node, as connections do.
    assert drafted == RuleMatch(
        True, 1, Rule("GEOSITE", "telegram", "低倍率", "tg"), ("低倍率", "香港 01"), ""
    )
    assert converted.skipped == (SkippedRule("RULE-SET,ads,REJECT", "rule_set"),)
    assert (converted.group_count, converted.merged_group_count, converted.entry_count) == (2, 1, 2)
    assert exported == "rules: []\n"
    preview_request, match_request, running_request, convert_request, export_request = received
    assert preview_request.preview_group.group_id == "cheap"
    assert preview_request.preview_group.composition.plan_json == COMPOSITION.plan_json
    draft = match_request.match_rule
    assert (draft.host, draft.port, draft.network, list(draft.overrides)) == (
        "t.me",
        80,
        "udp",
        ["mode: rule\n"],
    )
    assert draft.WhichOneof("source") == "composition"
    assert running_request.match_rule.WhichOneof("source") is None
    assert running == drafted
    assert convert_request.convert_to_plan == pb.ConvertToPlanRequest(
        yaml="rules: []\n", source_id="sub-a", locale="en"
    )
    assert export_request.export_plan.format == pb.EXPORT_FORMAT_GENERATED
    assert export_request.export_plan.options.mixed_port == 1


def test_simulated_match() -> None:
    host = SimulatedHost()
    config = TunnelConfig(composition=COMPOSITION)
    telegram = asyncio.run(host.match_rule("web.telegram.org", config=config))
    assert (telegram.index, telegram.rule) == (0, Rule("GEOSITE", "telegram", "低倍率", "tg"))
    lan = asyncio.run(host.match_rule("10.2.3.4", config=config))
    assert lan.rule is not None
    assert lan.rule.entry_id == "lan"
    other = asyncio.run(host.match_rule("example.org", config=config))
    assert other.rule == Rule("MATCH", "", "节点选择", "fallback")
    # The sample groups run, so the chain goes down to their node.
    assert other.chain == ("节点选择", "香港 01")
    with pytest.raises(HostError):
        asyncio.run(host.match_rule("not a host", config=config))
    asyncio.run(host.stop())
    with pytest.raises(HostError) as raised:
        asyncio.run(host.match_rule("example.org"))
    assert raised.value.code == "not_running"


def test_simulated_export() -> None:
    host = SimulatedHost()
    shared = yaml.safe_load(asyncio.run(host.export_plan(TunnelConfig(composition=COMPOSITION))))
    assert list(shared) == ["proxy-groups", "rules"]
    generated = asyncio.run(
        host.export_plan(TunnelConfig(composition=COMPOSITION), EXPORT_GENERATED)
    )
    assert "server: node0.example.net" in generated
