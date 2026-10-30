"""The host's side of "my plan" routing (docs/routing.md §8): compositions, InspectSource and
ListGeoCategories over the protocol, and the simulated host's stand-in for them."""

import asyncio
import base64
import json

import pytest

from solaris.host.base import (
    ComposedGroup,
    Composition,
    CompositionReport,
    CompositionSource,
    CompositionWarning,
    GeoCategories,
    HostError,
    PoolNode,
    Rule,
    RunOptions,
    SourceInspection,
    SourceNode,
    TunnelConfig,
    bundled_geo_categories,
)
from solaris.host.service import ServiceHost
from solaris.protocol import control_pb2
from tests.support.simulated import SAMPLE_CONFIG, SimulatedHost
from tests.test_control_client import serve

pb = control_pb2

COMPOSITION = Composition(
    sources=(CompositionSource("sub-a", "示例订阅 A", SAMPLE_CONFIG),),
    plan_json=json.dumps(
        {
            "version": 1,
            "rules": [
                {
                    "id": "tg",
                    "kind": "service",
                    "items": [{"type": "GEOSITE", "value": "telegram"}],
                    "target": "region:KR",
                },
                {
                    "id": "off",
                    "enabled": False,
                    "items": [{"type": "DOMAIN", "value": "x.example"}],
                    "target": "REJECT",
                },
            ],
            "fallback": "region:JP",
        }
    ),
    info_filter="剩余|到期",
)

REPORT = pb.CompositionReport(
    warnings=[pb.CompositionWarning(entry_id="tg", reason="region_empty", target="region:KR")],
    groups=[
        pb.ComposedGroup(id="proxy", name="节点选择", kind=pb.GROUP_KIND_BUILTIN),
        pb.ComposedGroup(
            id="region:JP", name="🇯🇵 日本", kind=pb.GROUP_KIND_REGION, region="JP", node_count=2
        ),
    ],
    rules=[pb.Rule(type="GEOSITE", payload="telegram", proxy="节点选择", entry_id="tg")],
    nodes=[
        pb.PoolNode(
            name="日本 01", original_name="日本 01", source_id="sub-a", type="Vless", region="JP"
        )
    ],
)


def test_a_config_carries_one_source() -> None:
    assert TunnelConfig(composition=COMPOSITION).config_yaml == ""
    assert TunnelConfig("mode: rule\n").composition is None
    with pytest.raises(ValueError, match="not both"):
        TunnelConfig("mode: rule\n", composition=COMPOSITION)


def test_service_host_sends_compositions_and_reads_the_report() -> None:
    received: list[pb.Request] = []

    async def answer(request: pb.Request) -> pb.Response:
        received.append(request)
        if request.WhichOneof("method") == "reload":
            return pb.Response(empty=pb.Empty())
        return pb.Response(composition=REPORT)

    options = RunOptions(mixed_port=7890)
    composed = TunnelConfig(overrides=("mode: global\n",), options=options, composition=COMPOSITION)

    async def scenario() -> list[CompositionReport | None]:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                return [
                    await host.validate(composed),
                    await host.start(composed),
                    await host.reload(TunnelConfig("mode: rule\n", options=options)),
                ]
            finally:
                await host.close()

    validated, started, reloaded = asyncio.run(scenario())
    assert reloaded is None
    assert (
        validated
        == started
        == CompositionReport(
            warnings=(CompositionWarning("tg", "region_empty", "region:KR"),),
            groups=(
                ComposedGroup("proxy", "节点选择", "builtin", "", 0),
                ComposedGroup("region:JP", "🇯🇵 日本", "region", "JP", 2),
            ),
            rules=(Rule("GEOSITE", "telegram", "节点选择", "tg"),),
            nodes=(PoolNode("日本 01", "日本 01", "sub-a", "Vless", "JP"),),
        )
    )
    validate, start, reload = received
    composition = validate.validate.composition
    assert validate.validate.WhichOneof("source") == "composition"
    assert [(s.id, s.name, s.yaml) for s in composition.sources] == [
        ("sub-a", "示例订阅 A", SAMPLE_CONFIG)
    ]
    assert (composition.info_filter, composition.plan_json) == (
        "剩余|到期",
        COMPOSITION.plan_json,
    )
    assert list(validate.validate.overrides) == ["mode: global\n"]
    assert start.start.WhichOneof("source") == "composition"
    assert start.start.options.mixed_port == 7890
    assert reload.reload.WhichOneof("source") == "config_yaml"
    assert reload.reload.config_yaml == "mode: rule\n"


def test_service_host_routing_methods() -> None:
    received: list[pb.Request] = []
    listed = pb.ListGeoCategoriesResponse(
        geosite=pb.GeoCategories(names=["cn", "telegram"], sha256="ab", updated_at_unix_ms=9),
        geoip=pb.GeoCategories(),  # the core has no GeoIP.dat
    )

    async def answer(request: pb.Request) -> pb.Response:
        received.append(request)
        if request.WhichOneof("method") == "inspect_source":
            if request.inspect_source.yaml == "broken":
                return pb.Response(error=pb.Error(code="invalid_config", message="no proxies"))
            node = pb.SourceNode(name="剩余流量", type="Http", region="ZZ", info=True)
            inspected = pb.InspectSourceResponse(
                nodes=[node], has_rules=True, group_count=1, rule_count=3
            )
            return pb.Response(inspect_source=inspected)
        return pb.Response(list_geo_categories=listed)

    async def scenario() -> tuple[SourceInspection, GeoCategories]:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                with pytest.raises(HostError) as raised:
                    await host.inspect_source("broken")
                assert raised.value.code == "invalid_config"
                return await host.inspect_source("proxies: []\n", "剩余"), (
                    await host.list_geo_categories()
                )
            finally:
                await host.close()

    inspection, categories = asyncio.run(scenario())
    assert inspection == SourceInspection((SourceNode("剩余流量", "Http", "ZZ", True),), True, 1, 3)
    assert received[1].inspect_source == pb.InspectSourceRequest(
        yaml="proxies: []\n", info_filter="剩余"
    )
    assert categories.geosite.names == ("cn", "telegram")
    assert (categories.geosite.sha256, categories.geosite.bundled) == ("ab", False)
    # The installer's list stands in for what the core cannot list.
    assert categories.geoip == bundled_geo_categories().geoip
    assert categories.geoip.bundled


def test_bundled_categories_hold_what_the_default_template_names() -> None:
    bundled = bundled_geo_categories()
    for name in ("cn", "private", "category-ads-all", "openai", "anthropic", "telegram"):
        assert name in bundled.geosite.names
    assert {"cn", "private", "telegram"} <= set(bundled.geoip.names)
    assert list(bundled.geosite.names) == sorted(set(bundled.geosite.names))
    assert len(bundled.geosite.sha256) == len(bundled.geoip.sha256) == 64


def test_simulated_host_composes() -> None:
    host = SimulatedHost(development=True)

    async def scenario() -> tuple[CompositionReport | None, CompositionReport | None]:
        validated = await host.validate(TunnelConfig(composition=COMPOSITION))
        await host.stop()
        return validated, await host.start(TunnelConfig(composition=COMPOSITION))

    validated, started = asyncio.run(scenario())
    assert validated == started == host.report
    assert validated is not None
    assert [g.id for g in validated.groups] == [
        "proxy",
        "auto",
        "region:HK",
        "region:JP",
        "region:SG",
        "region:US",
    ]
    assert [(r.type, r.payload, r.proxy, r.entry_id) for r in validated.rules] == [
        ("GEOSITE", "telegram", "节点选择", "tg"),
        ("MATCH", "", "🇯🇵 日本", "fallback"),
    ]
    assert validated.warnings == (CompositionWarning("tg", "region_empty", "region:KR"),)
    assert len(validated.nodes) == 9
    assert host.effective is not None
    assert host.effective["rules"] == ["GEOSITE,telegram,节点选择", "MATCH,🇯🇵 日本"]

    broken = Composition((), json.dumps({"version": 2}))
    with pytest.raises(HostError) as raised:
        asyncio.run(host.validate(TunnelConfig(composition=broken)))
    assert raised.value.code == "invalid_config"
    assert raised.value.message.startswith("plan: plan version 2 is not supported")


def test_simulated_host_inspects_and_lists_categories() -> None:
    host = SimulatedHost()
    inspection = asyncio.run(host.inspect_source(SAMPLE_CONFIG, "香港"))
    assert (inspection.has_rules, inspection.group_count, inspection.rule_count) == (True, 1, 1)
    assert inspection.nodes[0] == SourceNode("香港 01", "Vless", "HK", True)
    assert inspection.nodes[3] == SourceNode("日本 01", "Vless", "JP", False)

    links = "trojan://x@t.example:443#%F0%9F%87%B8%F0%9F%87%AC%20SG%2001\n"
    shared = asyncio.run(host.inspect_source(base64.b64encode(links.encode()).decode()))
    assert shared.nodes == (SourceNode("🇸🇬 SG 01", "Trojan", "SG", False),)
    assert not shared.has_rules

    categories = asyncio.run(host.list_geo_categories())
    assert not categories.geosite.bundled
    assert "telegram" in categories.geosite.names
    host.has_geodata = False
    assert asyncio.run(host.list_geo_categories()) == bundled_geo_categories()
