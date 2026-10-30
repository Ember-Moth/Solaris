"""The host's side of "my plan" routing (docs/routing.md §8): compositions, InspectSource and
ListGeoCategories over the protocol. The simulated host's stand-ins for the core's answers are
checked against the core in test_simulated.py."""

import asyncio
import json
from dataclasses import replace

import pytest

from solaris.host.base import (
    ComposedGroup,
    Composition,
    CompositionReport,
    CompositionSource,
    CompositionWarning,
    Egress,
    GeoCategories,
    HostError,
    MeshOutbound,
    PoolNode,
    Rule,
    RunOptions,
    SourceInspection,
    SourceNode,
    TunnelConfig,
    VpnGateServer,
    WarpAccount,
    bundled_geo_categories,
)
from solaris.host.service import ServiceHost
from solaris.protocol import control_pb2
from tests.support.simulated import (
    SAMPLE_CONFIG,
    VPNGATE_SERVERS,
    SimulatedHost,
)
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


WARP = WarpAccount(
    device_id="dev-1",
    token="tok",
    protocol="wireguard",
    private_key="priv",
    peer_public_key="peer",
    endpoint="162.159.192.1:2408",
    address_v4="172.16.0.2",
    address_v6="2606:4700:110::2",
    client_id="AQID",
    registered_at_unix_ms=1,
)


def test_service_host_carries_exits_and_mesh_outbounds() -> None:
    """The second round's contract (docs/exits.md §7, docs/mesh.md §6): the WARP account
    rides in the composition, RegisterWarp answers with one, and the report lists the mesh
    outbounds."""
    received: list[pb.Request] = []
    mesh = pb.MeshOutbound(id="mesh:sub-a/家里", name="家里", type="Tailscale", source_id="sub-a")

    async def answer(request: pb.Request) -> pb.Response:
        received.append(request)
        if request.WhichOneof("method") == "register_warp":
            account = pb.WarpAccount(device_id="dev-2", token="t2", private_key="k2")
            return pb.Response(register_warp=pb.RegisterWarpResponse(account=account))
        return pb.Response(composition=pb.CompositionReport(mesh=[mesh]))

    composition = Composition(
        sources=COMPOSITION.sources, plan_json=COMPOSITION.plan_json, egress=Egress(warp=WARP)
    )

    async def scenario() -> tuple[CompositionReport | None, WarpAccount]:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                report = await host.validate(TunnelConfig(composition=composition))
                return report, await host.register_warp("节点选择", "wireguard", WARP)
            finally:
                await host.close()

    report, account = asyncio.run(scenario())
    assert report is not None
    assert report.mesh == (MeshOutbound("mesh:sub-a/家里", "家里", "Tailscale", "sub-a"),)
    sent = received[0].validate.composition.egress.warp
    assert (sent.device_id, sent.protocol, sent.endpoint, sent.client_id) == (
        "dev-1",
        pb.WARP_PROTOCOL_WIREGUARD,
        "162.159.192.1:2408",
        "AQID",
    )
    assert received[1].register_warp == pb.RegisterWarpRequest(
        via="节点选择", protocol=pb.WARP_PROTOCOL_WIREGUARD, device_id="dev-1", token="tok"
    )
    # An unspecified protocol is the default, MASQUE over HTTP/2.
    assert (account.device_id, account.protocol, account.private_key) == (
        "dev-2",
        "masque-h2",
        "k2",
    )


def test_a_composition_without_exits_sends_none() -> None:
    assert not pb.Composition().HasField("egress")
    assert COMPOSITION.egress == Egress()


def test_bundled_categories_hold_what_the_default_template_names() -> None:
    bundled = bundled_geo_categories()
    for name in ("cn", "private", "category-ads-all", "openai", "anthropic", "telegram"):
        assert name in bundled.geosite.names
    assert {"cn", "private", "telegram"} <= set(bundled.geoip.names)
    assert list(bundled.geosite.names) == sorted(set(bundled.geosite.names))
    assert len(bundled.geosite.sha256) == len(bundled.geoip.sha256) == 64


def test_simulated_host_lists_categories() -> None:
    host = SimulatedHost()
    categories = asyncio.run(host.list_geo_categories())
    assert not categories.geosite.bundled
    assert "telegram" in categories.geosite.names
    host.has_geodata = False
    assert asyncio.run(host.list_geo_categories()) == bundled_geo_categories()


def test_service_host_carries_the_vpngate_servers() -> None:
    """ListVpnGate's servers come back as the UI keeps them, and go to the core unchanged in
    the composition (docs/exits.md §7)."""
    received: list[pb.Request] = []
    server = VPNGATE_SERVERS[0]

    async def answer(request: pb.Request) -> pb.Response:
        received.append(request)
        if request.WhichOneof("method") == "list_vpn_gate":
            listed = pb.VpnGateServer(
                host_name=server.host_name,
                ip=server.ip,
                country=server.country,
                score=server.score,
                ping_ms=server.ping_ms,
                speed_bps=server.speed_bps,
                sessions=server.sessions,
                uptime_ms=server.uptime_ms,
                operator=server.operator,
                openvpn=pb.OpenVpnOptions(
                    server=server.openvpn.server,
                    port=server.openvpn.port,
                    proto="tcp",
                    cipher="AES-128-CBC",
                    data_ciphers=["AES-128-CBC"],
                    auth="SHA1",
                    ca=server.openvpn.ca,
                    cert=server.openvpn.cert,
                    key=server.openvpn.key,
                ),
            )
            return pb.Response(list_vpn_gate=pb.ListVpnGateResponse(servers=[listed]))
        return pb.Response(composition=pb.CompositionReport())

    async def scenario() -> list[VpnGateServer]:
        listening, opener = await serve(answer)
        async with listening:
            host = ServiceHost(opener)
            try:
                servers = await host.list_vpngate("节点选择")
                composition = replace(COMPOSITION, egress=Egress(vpngate=tuple(servers)))
                await host.validate(TunnelConfig(composition=composition))
                return servers
            finally:
                await host.close()

    servers = asyncio.run(scenario())
    assert servers == [server]
    assert received[0].list_vpn_gate == pb.ListVpnGateRequest(via="节点选择")
    sent = received[1].validate.composition.egress.vpn_gate
    assert [s.host_name for s in sent] == [server.host_name]
    assert (sent[0].openvpn.server, list(sent[0].openvpn.data_ciphers)) == (
        "203.0.113.11",
        ["AES-128-CBC"],
    )
