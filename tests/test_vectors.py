"""Cross-language samples shared with src/core/internal/control/vectors_test.go."""

import json
from pathlib import Path

from mihomo_desktop.protocol import control_pb2
from mihomo_desktop.protocol.frame import (
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

# The requests, as the UI encodes them; Go checks what it decodes from each.
REQUESTS = {
    "get_info": pb.Request(protocol_version=1, id=1, get_info=pb.GetInfoRequest()),
    "validate_composition": pb.Request(
        protocol_version=1,
        id=2,
        validate=pb.ValidateRequest(
            composition=pb.Composition(
                sources=[pb.CompositionSource(id="sub-a", name="示例订阅 A", yaml="proxies: []\n")],
                info_filter="剩余|到期",
                plan_json='{"version": 1}',
            ),
            overrides=["mode: rule\n"],
        ),
    ),
    "inspect_source": pb.Request(
        protocol_version=1,
        id=3,
        inspect_source=pb.InspectSourceRequest(yaml="proxies: []\n", info_filter="剩余"),
    ),
    "list_geo_categories": pb.Request(
        protocol_version=1, id=4, list_geo_categories=pb.ListGeoCategoriesRequest()
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
