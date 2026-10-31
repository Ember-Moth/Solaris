"""Cross-language samples shared with src/core/internal/control/vectors_test.go."""

import json
from pathlib import Path

from solaris.protocol import control_pb2
from solaris.protocol.frame import (
    HEADER_SIZE,
    KIND_REQUEST,
    KIND_RESPONSE,
    encode,
    parse_header,
)

pb = control_pb2

VECTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "testdata/control-vectors.json").read_text("utf-8")
)

COMPOSITION = pb.Composition(
    sources=[pb.CompositionSource(id="sub-a", name="示例订阅 A", yaml="proxies: []\n")],
    info_filter="剩余|到期",
    plan_json='{"version": 1}',
)

# The requests, as the UI encodes them; Go checks what it decodes from each.
REQUESTS = {
    "get_info": pb.Request(protocol_version=1, id=1, get_info=pb.GetInfoRequest()),
    "expired": pb.Request(
        protocol_version=1, id=9, timeout_ms=5000, deadline_unix_ms=1, get_info=pb.GetInfoRequest()
    ),
    "recovery_status": pb.Request(protocol_version=1, id=10, get_status=pb.GetStatusRequest()),
    "validate_composition": pb.Request(
        protocol_version=1,
        id=2,
        validate=pb.ValidateRequest(composition=COMPOSITION, overrides=["mode: rule\n"]),
    ),
    "inspect_source": pb.Request(
        protocol_version=1,
        id=3,
        inspect_source=pb.InspectSourceRequest(yaml="proxies: []\n", info_filter="剩余"),
    ),
    "list_geo_categories": pb.Request(
        protocol_version=1, id=4, list_geo_categories=pb.ListGeoCategoriesRequest()
    ),
    "preview_group": pb.Request(
        protocol_version=1,
        id=5,
        preview_group=pb.PreviewGroupRequest(composition=COMPOSITION, group_id="g-1"),
    ),
    "match_rule": pb.Request(
        protocol_version=1,
        id=6,
        match_rule=pb.MatchRuleRequest(
            composition=COMPOSITION,
            overrides=["mode: rule\n"],
            host="example.com",
            port=80,
            network="udp",
        ),
    ),
    "convert_to_plan": pb.Request(
        protocol_version=1,
        id=7,
        convert_to_plan=pb.ConvertToPlanRequest(
            yaml="rules: []\n", source_id="sub-a", locale="en", info_filter="剩余"
        ),
    ),
    "export_plan": pb.Request(
        protocol_version=1,
        id=8,
        export_plan=pb.ExportPlanRequest(
            composition=COMPOSITION,
            format=pb.EXPORT_FORMAT_GENERATED,
            overrides=["mode: rule\n"],
            options=pb.RunOptions(mixed_port=7890, tun_stack=pb.TUN_STACK_GVISOR),
        ),
    ),
}


def response(name: str) -> pb.Response:
    frame = bytes.fromhex(VECTORS[f"{name}_response_frame"])
    length = parse_header(frame[:HEADER_SIZE], KIND_RESPONSE)
    return pb.Response.FromString(frame[HEADER_SIZE : HEADER_SIZE + length])


def test_request_encoding_matches_go() -> None:
    for name, request in REQUESTS.items():
        frame = encode(KIND_REQUEST, request.SerializeToString())
        assert frame.hex() == VECTORS[f"{name}_request_frame"], name


def test_decodes_go_response() -> None:
    info = response("get_info")
    assert info.id == 1
    assert info.get_info.core_version == "unlinked"
    assert info.get_info.state == pb.RUN_STATE_STOPPED
    assert info.get_info.ownership == pb.OWNERSHIP_AVAILABLE


def test_decodes_go_recovery_status() -> None:
    status = response("recovery_status").get_status
    assert status.state == pb.RUN_STATE_STOPPED
    assert status.recovery_state == pb.RECOVERY_STATE_FAILED
    assert status.recovery_error == "network_unavailable"


def test_decodes_go_composition_report() -> None:
    validated = response("validate_composition")
    assert (validated.id, validated.WhichOneof("result")) == (2, "composition")
    report = validated.composition
    assert [(w.entry_id, w.reason, w.target) for w in report.warnings] == [
        ("tg", "region_empty", "region:US")
    ]
    assert [(g.id, g.name, g.kind, g.region, g.node_count) for g in report.groups] == [
        ("proxy", "节点选择", pb.GROUP_KIND_BUILTIN, "", 0),
        ("region:HK", "🇭🇰 香港", pb.GROUP_KIND_REGION, "HK", 1),
    ]
    (rule,) = report.rules
    assert (rule.type, rule.payload, rule.proxy, rule.entry_id) == (
        "GEOSITE",
        "telegram",
        "节点选择",
        "tg",
    )
    (node,) = report.nodes
    assert (node.name, node.source_id, node.type, node.region) == (
        "香港 01",
        "sub-a",
        "Shadowsocks",
        "HK",
    )


def test_decodes_go_routing_methods() -> None:
    inspected = response("inspect_source").inspect_source
    assert (inspected.has_rules, inspected.group_count, inspected.rule_count) == (True, 1, 2)
    (node,) = inspected.nodes
    assert (node.name, node.type, node.region, node.info) == ("剩余流量", "Http", "ZZ", True)

    categories = response("list_geo_categories").list_geo_categories
    assert list(categories.geosite.names) == ["cn", "telegram"]
    assert categories.geosite.sha256 == "ab" + "0" * 62
    assert categories.geosite.updated_at_unix_ms == 1761696000000
    assert list(categories.geoip.names) == ["cn"]
    assert categories.geoip.sha256 == ""


def test_decodes_go_plan_methods() -> None:
    preview = response("preview_group").preview_group
    (node,) = preview.nodes
    assert (node.name, node.source_id, node.region) == ("香港 01", "sub-a", "HK")
    assert list(preview.others) == ["DIRECT"]

    matched = response("match_rule").match_rule
    assert (matched.matched, matched.index, matched.resolved_ip) == (True, 2, "192.0.2.1")
    rule = matched.rule
    assert (rule.type, rule.payload, rule.proxy, rule.entry_id) == (
        "DOMAIN-SUFFIX",
        "example.com",
        "节点选择",
        "web",
    )
    assert list(matched.chain) == ["香港 01", "节点选择"]

    converted = response("convert_to_plan").convert_to_plan
    assert converted.plan_json == '{"version":1}'
    assert (
        converted.group_count,
        converted.merged_group_count,
        converted.rule_count,
        converted.raw_rule_count,
        converted.entry_count,
    ) == (3, 1, 5, 1, 3)
    assert [(s.rule, s.reason) for s in converted.skipped] == [("RULE-SET,ads,REJECT", "rule_set")]

    assert response("export_plan").export_plan.yaml == "proxy-groups: []\n"
