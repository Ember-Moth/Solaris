"""The simulated host's stand-ins against the core: testdata/core-vectors.json holds what the
core answers for each input (src/core/internal/compose/vectors_test.go keeps it current), and
the simulation must answer the same, so the UI's tests do not rely on answers the core never
gives."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from google.protobuf import json_format

from solaris.host import service
from solaris.host.base import (
    Composition,
    CompositionSource,
    EasyTierSettings,
    Egress,
    GroupPreview,
    HostError,
    MeshNetwork,
    MeshSettings,
    PlanConversion,
    SkippedRule,
    SourceInspection,
    SourceNode,
    TailscaleSettings,
    TunnelConfig,
    ZeroTierSettings,
)
from solaris.protocol import control_pb2
from tests.support.simulated import SimulatedHost

pb = control_pb2

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "testdata/core-vectors.json").read_text("utf-8")
)


def names(section: str) -> list[str]:
    return [vector["name"] for vector in VECTORS[section]]


def vector(section: str, name: str) -> dict[str, Any]:
    return next(v for v in VECTORS[section] if v["name"] == name)


def mesh_network(message: pb.MeshNetwork) -> MeshNetwork:
    settings: MeshSettings
    match message.WhichOneof("network"):
        case "tailscale":
            ts = message.tailscale
            settings = TailscaleSettings(ts.auth_key, ts.control_url, ts.hostname)
        case "zerotier":
            zt = message.zerotier
            settings = ZeroTierSettings(zt.network_id, zt.identity_secret)
        case _:
            et = message.easytier
            settings = EasyTierSettings(
                et.network_name, et.network_secret, tuple(et.peers), et.ipv4, et.hostname
            )
    return MeshNetwork(message.id, message.name, settings, message.via)


def composition(raw: dict[str, Any]) -> Composition:
    """The vector's composition as the UI sends it; ServiceHost encodes it back unchanged."""
    message = json_format.ParseDict(raw, pb.Composition())
    egress = message.egress
    decoded = Composition(
        tuple(CompositionSource(s.id, s.name, s.yaml) for s in message.sources),
        message.plan_json,
        message.info_filter,
        Egress(
            service._warp_account_from(egress.warp) if egress.HasField("warp") else None,
            tuple(service._vpngate_server_from(s) for s in egress.vpn_gate),
        ),
        tuple(mesh_network(n) for n in message.mesh),
    )
    assert service._composition(decoded) == message
    return decoded


def refused(error: str) -> tuple[str, str]:
    return ("invalid_config", error)


@pytest.mark.parametrize("name", names("compose"))
def test_composition(name: str) -> None:
    case = vector("compose", name)
    config = TunnelConfig(composition=composition(case["composition"]))
    if "error" in case:
        with pytest.raises(HostError) as raised:
            asyncio.run(SimulatedHost().validate(config))
        assert (raised.value.code, raised.value.message) == refused(case["error"])
        return
    report = json_format.ParseDict(case["report"], pb.CompositionReport())
    expected = service._report(pb.Response(composition=report))
    assert asyncio.run(SimulatedHost().validate(config)) == expected


@pytest.mark.parametrize("name", names("inspect"))
def test_inspection(name: str) -> None:
    case = vector("inspect", name)
    answer = json_format.ParseDict(case["inspection"], pb.InspectSourceResponse())
    expected = SourceInspection(
        tuple(SourceNode(n.name, n.type, n.region, n.info, n.mesh) for n in answer.nodes),
        answer.has_rules,
        answer.group_count,
        answer.rule_count,
    )
    host = SimulatedHost()
    assert asyncio.run(host.inspect_source(case["yaml"], case["info_filter"])) == expected


@pytest.mark.parametrize("name", names("preview"))
def test_preview(name: str) -> None:
    case = vector("preview", name)
    asked = composition(case["composition"]), case["group_id"]
    if "error" in case:
        with pytest.raises(HostError) as raised:
            asyncio.run(SimulatedHost().preview_group(*asked))
        assert (raised.value.code, raised.value.message) == refused(case["error"])
        return
    answer = json_format.ParseDict(case["preview"], pb.PreviewGroupResponse())
    expected = GroupPreview(
        tuple(service._pool_node(n) for n in answer.nodes), tuple(answer.others)
    )
    assert asyncio.run(SimulatedHost().preview_group(*asked)) == expected


def plan_outline(plan_json: str) -> dict[str, Any]:
    """What the UI's tests read of a converted plan: its groups by name, type and other
    members, and its rules by kind, items and target. Which nodes a group picks, and the
    ids, are the core's own; tests/e2e/test_plans_ui.py converts through the real core."""
    plan = json.loads(plan_json)
    groups = plan.get("groups") or []
    by_id = {group["id"]: group["name"] for group in groups}
    return {
        "groups": [
            (g["name"], g["type"], [by_id.get(o, o) for o in g["members"].get("others", [])])
            for g in groups
        ],
        "rules": [
            (r["kind"], r["items"], by_id.get(r["target"], r["target"]))
            for r in plan.get("rules") or []
        ],
        "fallback": by_id.get(plan.get("fallback", "proxy"), plan.get("fallback", "proxy")),
    }


def conversion_outline(conversion: PlanConversion) -> dict[str, Any]:
    return {
        "plan": plan_outline(conversion.plan_json),
        "counts": (
            conversion.group_count,
            conversion.merged_group_count,
            conversion.rule_count,
            conversion.raw_rule_count,
            conversion.entry_count,
        ),
        "skipped": conversion.skipped,
    }


@pytest.mark.parametrize("name", names("convert"))
def test_conversion(name: str) -> None:
    case = vector("convert", name)
    request = json_format.ParseDict(case["request"], pb.ConvertToPlanRequest())
    asked = SimulatedHost().convert_to_plan(
        request.yaml,
        source_id=request.source_id,
        locale=request.locale or "zh",
        info_filter=request.info_filter,
    )
    if "error" in case:
        with pytest.raises(HostError) as raised:
            asyncio.run(asked)
        assert (raised.value.code, raised.value.message) == refused(case["error"])
        return
    answer = json_format.ParseDict(case["conversion"], pb.ConvertToPlanResponse())
    expected = PlanConversion(
        plan_json=answer.plan_json,
        group_count=answer.group_count,
        merged_group_count=answer.merged_group_count,
        rule_count=answer.rule_count,
        raw_rule_count=answer.raw_rule_count,
        entry_count=answer.entry_count,
        skipped=tuple(SkippedRule(s.rule, s.reason) for s in answer.skipped),
    )
    assert conversion_outline(asyncio.run(asked)) == conversion_outline(expected)
