"""The host's side of "my plan" routing (docs/routing.md §8): compositions, InspectSource and
ListGeoCategories over the protocol, and the simulated host's stand-in for them."""

import asyncio
import base64
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
    check_config,
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


MESH_SOURCE = """proxies:
  - {name: 家里, type: tailscale, auth-key: tskey-sample}
  - {name: 香港 01, type: easytier, network-name: home}
"""


def test_simulated_host_keeps_mesh_outbounds_apart_and_rejects_without_exits() -> None:
    """docs/mesh.md §3 and docs/exits.md §3.3, as the core does them."""
    plan = {
        "version": 1,
        "rules": [
            {
                "id": "ts",
                "items": [{"type": "DOMAIN-SUFFIX", "value": "ts.net"}],
                "target": "mesh:home/家里",
            },
            {
                "id": "gone",
                "items": [{"type": "DOMAIN", "value": "nas.lan"}],
                "target": "mesh:home/旧的",
            },
            {
                "id": "ai",
                "items": [{"type": "GEOSITE", "value": "openai"}],
                "target": "egress:warp",
            },
        ],
        "fallback": "proxy",
        "egress": {"warp": {"via": "region:KR"}},
    }
    sources = (*COMPOSITION.sources, CompositionSource("home", "家", MESH_SOURCE))
    composition = Composition(sources, json.dumps(plan))
    host = SimulatedHost()
    report = asyncio.run(host.validate(TunnelConfig(composition=composition)))
    assert report is not None
    assert report.mesh == (
        MeshOutbound("mesh:home/家里", "家里", "Tailscale", "home"),
        # The name a pool node holds already gets the profile's.
        MeshOutbound("mesh:home/香港 01", "香港 01 · 家", "EasyTier", "home"),
    )
    assert len(report.nodes) == 9
    assert [(r.payload, r.proxy) for r in report.rules[:3]] == [
        ("ts.net", "家里"),
        ("nas.lan", "REJECT"),
        ("openai", "REJECT"),
    ]
    assert report.warnings == (
        CompositionWarning("gone", "mesh_missing", "mesh:home/旧的"),
        CompositionWarning("ai", "egress_unavailable", "egress:warp"),
    )
    # With an account the exit goes out through its via group, or 节点选择 without nodes.
    composition = replace(composition, egress=Egress(warp=WARP))
    report = asyncio.run(host.validate(TunnelConfig(composition=composition)))
    assert report is not None
    assert report.rules[2].proxy == "WARP 出口"
    assert report.warnings[-1] == CompositionWarning("egress:warp", "via_empty", "region:KR")
    document = check_config(TunnelConfig(composition=composition))
    warp = next(p for p in document["proxies"] if p["name"] == "WARP 出口")
    assert (warp["type"], warp["dialer-proxy"]) == ("wireguard", "节点选择")
    inspection = asyncio.run(host.inspect_source(MESH_SOURCE))
    assert [(n.name, n.mesh) for n in inspection.nodes] == [("家里", True), ("香港 01", True)]


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


def test_simulated_list_vpngate_goes_through_any_outbound() -> None:
    """As in the core, the list may go out through any outbound of the running configuration:
    a group, a node or a built-in one; nothing else."""
    host = SimulatedHost()

    async def scenario() -> list[str | None]:
        report = await host.reload(TunnelConfig(composition=COMPOSITION))
        assert report is not None
        outcomes: list[str | None] = []
        for via in (report.groups[0].name, report.nodes[0].name, "DIRECT", "没有这个"):
            try:
                await host.list_vpngate(via)
                outcomes.append(None)
            except HostError as error:
                outcomes.append(error.code)
        return outcomes

    assert asyncio.run(scenario()) == [None, None, None, "invalid_request"]
    assert len(host.vpngate_calls) == 3
