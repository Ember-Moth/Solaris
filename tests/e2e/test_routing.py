"""Routing with "my plan" on the development service (docs/routing.md §8). The core
composes the configuration from a subscription with only nodes and a plan; requests through
the mixed port go where the plan's rules send them, and the connections name the rule entry.

The pool's nodes are the local HTTP proxies of tests/e2e/servers.py, and the core reads the
GEO data the installers ship (scripts/fetch_geodata.py, cached in build/geodata).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Awaitable, Callable

import pytest

from scripts.dev import DevService
from scripts.fetch_geodata import GEOIP, GEOSITE, seed
from solaris.host.base import (
    EXPORT_GENERATED,
    Composition,
    CompositionSource,
    CompositionWarning,
    Connection,
    HostError,
    RunOptions,
    SourceNode,
    TunnelConfig,
    bundled_geo_categories,
)
from solaris.host.service import ServiceHost
from tests.e2e.app import Network, Stream
from tests.e2e.servers import ProxyNode, WebServer, free_port
from tests.e2e.unix import unix_opener

pytestmark = pytest.mark.e2e


def nodes_only(network: Network) -> str:
    """A subscription with nodes and nothing else, as many airports serve."""
    return f"""\
proxies:
  - {{name: 🇭🇰 香港 A, type: http, server: 127.0.0.1, port: {network.node_a.port}}}
  - {{name: 🇯🇵 日本 B, type: http, server: 127.0.0.1, port: {network.node_b.port}}}
  - {{name: 剩余流量 12 GB, type: http, server: 127.0.0.1, port: 9}}
"""


def plan(test_url: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "test_url": test_url,
            "groups": [
                {
                    "id": "mine",
                    "kind": "custom",
                    "name": "我的节点",
                    "members": {"nodes": [{"source": "sub", "name": "🇭🇰 香港 A"}]},
                }
            ],
            "rules": [
                {
                    "id": "off",
                    "kind": "domain",
                    "enabled": False,
                    "items": [{"type": "DOMAIN", "value": "localhost"}],
                    "target": "REJECT",
                },
                {
                    "id": "jp",
                    "kind": "domain",
                    "items": [{"type": "DOMAIN", "value": "localhost"}],
                    "target": "region:JP",
                },
                {
                    "id": "loopback",
                    "kind": "ip",
                    "items": [{"type": "IP-CIDR", "value": "127.0.0.1/32", "no_resolve": True}],
                    "target": "mine",
                },
                {
                    "id": "us",
                    "kind": "domain",
                    "items": [{"type": "DOMAIN-SUFFIX", "value": "us.example"}],
                    "target": "region:US",
                },
                {
                    "id": "geo",
                    "kind": "service",
                    "items": [
                        {"type": "GEOSITE", "value": "telegram"},
                        {"type": "GEOIP", "value": "telegram", "no_resolve": True},
                    ],
                    "target": "region:JP",
                },
            ],
            "fallback": "DIRECT",
        }
    )


def curl(mixed_port: int, url: str) -> str:
    done = subprocess.run(
        ["curl", "-sS", "--max-time", "5", "-x", f"http://127.0.0.1:{mixed_port}", url],
        capture_output=True,
        text=True,
        check=False,
    )
    return (done.stdout or done.stderr).strip()


def on_service[T](service: DevService, scenario: Callable[[ServiceHost], Awaitable[T]]) -> T:
    async def run() -> T:
        host = ServiceHost(unix_opener(str(service.socket)))
        try:
            return await scenario(host)
        finally:
            await host.close()

    return asyncio.run(run())


def tunnels(node: ProxyNode, server: WebServer) -> int:
    """How many tunnels the node opened to the server; mihomo dials localhost by the
    address its hosts table gives it."""
    return sum(target.endswith(f":{server.port}") for target in node.targets)


async def connection_to(host: ServiceHost, name: str) -> Connection:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for connection in await host.poll_connections():
            if connection.host == name:
                return connection
        await asyncio.sleep(0.2)
    raise AssertionError(f"no connection to {name}")


def test_plan_routes_a_nodes_only_subscription(service: DevService, network: Network) -> None:
    """Import a subscription with only nodes, connect with a plan, and a domain rule sends
    the request to its region's node; the connection names the rule entry."""
    seed(service.home)
    mixed_port = free_port()
    subscription = nodes_only(network)
    with WebServer("health") as health:  # the url-test groups' delay tests go here
        composition = Composition(
            (CompositionSource("sub", "测试机场", subscription),),
            plan(health.url("/generate_204")),
            "剩余|到期",
        )
        on_service(service, lambda host: routes(host, network, composition, mixed_port))


async def routes(
    host: ServiceHost, network: Network, composition: Composition, mixed_port: int
) -> None:
    proxied = network.proxied.port
    subscription = composition.sources[0].yaml
    inspection = await host.inspect_source(subscription, "剩余|到期")
    assert not inspection.has_rules
    assert inspection.nodes == (
        SourceNode("🇭🇰 香港 A", "Http", "HK", False),
        SourceNode("🇯🇵 日本 B", "Http", "JP", False),
        SourceNode("剩余流量 12 GB", "Http", "ZZ", True),
    )

    report = await host.start(
        TunnelConfig(options=RunOptions(mixed_port=mixed_port), composition=composition)
    )
    assert report is not None
    assert report.warnings == (CompositionWarning("us", "region_empty", "region:US"),)
    assert [(g.id, g.name, g.node_count) for g in report.groups] == [
        ("proxy", "节点选择", 0),
        ("auto", "自动选择", 2),
        ("region:HK", "🇭🇰 香港", 1),
        ("region:JP", "🇯🇵 日本", 1),
        ("mine", "我的节点", 1),
    ]
    assert [(r.type, r.proxy, r.entry_id) for r in report.rules] == [
        ("DOMAIN", "🇯🇵 日本", "jp"),
        ("IP-CIDR", "我的节点", "loopback"),
        ("DOMAIN-SUFFIX", "节点选择", "us"),
        ("GEOSITE", "🇯🇵 日本", "geo"),
        ("GEOIP", "🇯🇵 日本", "geo"),
        ("MATCH", "DIRECT", "fallback"),
    ]
    groups = {group.name: group for group in await host.list_groups()}
    assert [p.name for p in groups["我的节点"].proxies] == ["🇭🇰 香港 A"]
    assert [p.name for p in groups["自动选择"].proxies] == ["🇭🇰 香港 A", "🇯🇵 日本 B"]

    # The domain rule: localhost goes to Japan, which is node-b.
    stream = await asyncio.to_thread(
        Stream, mixed_port, f"http://localhost:{proxied}/slow?seconds=3"
    )
    connection = await connection_to(host, "localhost")
    assert connection.rule == "DOMAIN,localhost"
    assert connection.rule_entry_id == "jp"
    assert connection.chains == ("🇯🇵 日本", "🇯🇵 日本 B")
    assert await asyncio.to_thread(stream.done.wait, 10)
    assert stream.error == ""
    assert tunnels(network.node_b, network.proxied) == 1
    assert tunnels(network.node_a, network.proxied) == 0

    # The IP rule: 127.0.0.1 goes to the custom group, which holds node-a alone.
    assert await asyncio.to_thread(curl, mixed_port, network.direct.url()) == "direct"
    assert tunnels(network.node_a, network.direct) == 1
    assert tunnels(network.node_b, network.direct) == 0

    # The plan changes: with the Japan rule off, localhost, which mihomo's hosts table maps
    # to 127.0.0.1, meets the loopback rule and goes to node-a.
    changed = json.loads(composition.plan_json)
    changed["rules"][1]["enabled"] = False
    reloaded = await host.reload(
        TunnelConfig(
            options=RunOptions(mixed_port=mixed_port),
            composition=Composition(composition.sources, json.dumps(changed), "剩余|到期"),
        )
    )
    assert reloaded is not None
    assert "jp" not in {rule.entry_id for rule in reloaded.rules}
    url = f"http://localhost:{proxied}/"
    assert await asyncio.to_thread(curl, mixed_port, url) == "proxied"
    assert tunnels(network.node_b, network.proxied) == 1
    assert tunnels(network.node_a, network.proxied) == 1


def test_core_lists_the_categories_of_its_geo_data(service: DevService) -> None:
    """Without GEO data the host falls back to the installer's list; with the installer's data
    the core reads the same names from the files."""
    bundled = bundled_geo_categories()

    async def listed(host: ServiceHost) -> tuple[bool, bool]:
        categories = await host.list_geo_categories()
        return categories.geosite.bundled, categories.geoip.bundled

    assert on_service(service, listed) == (True, True)
    seed(service.home)
    categories = on_service(service, lambda host: host.list_geo_categories())
    assert not categories.geosite.bundled
    assert not categories.geoip.bundled
    assert categories.geosite.names == bundled.geosite.names
    assert categories.geoip.names == bundled.geoip.names
    assert (categories.geosite.sha256, categories.geoip.sha256) == (GEOSITE.sha256, GEOIP.sha256)


def test_core_judges_categories_by_its_data(service: DevService) -> None:
    """A category the data does not hold is an invalid configuration, in mihomo's words."""
    seed(service.home)

    def composition(category: str) -> TunnelConfig:
        rule = {"id": "r", "items": [{"type": "GEOSITE", "value": category}], "target": "DIRECT"}
        return TunnelConfig(
            composition=Composition((), json.dumps({"version": 1, "rules": [rule]}))
        )

    async def verdicts(host: ServiceHost) -> tuple[str, str]:
        report = await host.validate(composition("category-ads-all"))
        assert report is not None
        assert report.rules[0].payload == "category-ads-all"
        try:
            await host.validate(composition("no-such-category"))
        except HostError as error:
            return error.code, error.message
        return "valid", ""

    code, message = on_service(service, verdicts)
    assert code == "invalid_config"
    assert "no-such-category" in message


def test_the_generated_configuration_leaves_out_node_secrets(service: DevService) -> None:
    """ExportPlan's whole configuration shows each node by name, type, server and port only
    (docs/routing.md §8.2); the routing page's 查看生成的配置 shows it as it comes."""
    seed(service.home)
    subscription = (
        "proxies:\n"
        "  - {name: 香港 01, type: ss, server: hk.example.net, port: 443,"
        " cipher: aes-128-gcm, password: hunter2}\n"
    )
    config = TunnelConfig(
        composition=Composition(
            (CompositionSource("sub", "测试机场", subscription),), json.dumps({"version": 1})
        )
    )
    generated = on_service(service, lambda host: host.export_plan(config, EXPORT_GENERATED))
    assert "hunter2" not in generated
    assert "aes-128-gcm" not in generated
    assert "server: hk.example.net" in generated
