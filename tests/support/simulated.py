"""An in-process TunnelHost, subscription fetcher and profiles with the prototype's
sample data.

Used by the tests and scripts/preview.py so the UI runs without the service, the system
extension or the network. Traffic follows a seeded random walk; connections open and close
and log lines arrive as the clock advances, all from seeded generators, so runs with a fake
clock are repeatable.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import json
import random
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from solaris.host.base import (
    EXPORT_GENERATED,
    EXPORT_SHARE,
    LOG_LEVELS,
    TAKEOVER_SETTINGS,
    TUN_STACKS,
    ComposedGroup,
    Composition,
    CompositionReport,
    CompositionWarning,
    Connection,
    EasyTierSettings,
    GeoCategories,
    GroupPreview,
    HostError,
    HostInfo,
    HostStatus,
    LogEntry,
    MeshNetwork,
    MeshOutbound,
    MeshStatus,
    OpenVpnOptions,
    PlanConversion,
    PoolNode,
    Proxy,
    ProxyGroup,
    Rule,
    RuleMatch,
    RunOptions,
    SkippedRule,
    SourceInspection,
    SourceNode,
    TailscaleSettings,
    TrafficSample,
    TunnelConfig,
    VpnGateServer,
    WarpAccount,
    ZeroTierSettings,
    bundled_geo_categories,
    taken_over,
)
from solaris.models.plans import GROUP_TYPES
from solaris.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from solaris.models.routing import MODE_PROFILE, RoutingStore
from solaris.models.store import ProfileStore
from solaris.models.subscription import Download

GB = 1024**3
MB = 1024**2

NODES: dict[str, tuple[str, int]] = {  # a delay of 0 is a failed test
    "香港 01": ("VLESS", 68),
    "香港 02": ("Hysteria2", 54),
    "香港 03": ("Trojan", 182),
    "日本 01": ("VLESS", 96),
    "日本 02": ("Shadowsocks", 241),
    "新加坡 01": ("Trojan", 127),
    "新加坡 02": ("VLESS", 0),
    "美国 01": ("Hysteria2", 198),
    "美国 02": ("Shadowsocks", 412),
}


# The insert keys of an override (docs/ui.md §4): key -> (target list, at front).
INSERT_KEYS = {
    "prepend-rules": ("rules", True),
    "append-rules": ("rules", False),
    "prepend-proxies": ("proxies", True),
    "append-proxies": ("proxies", False),
    "prepend-proxy-groups": ("proxy-groups", True),
    "append-proxy-groups": ("proxy-groups", False),
}
# Cleared after the overrides (architecture §3.3, item 2), so no override can set them.
CONTROLLER_KEYS = (
    "external-controller",
    "external-controller-tls",
    "external-controller-unix",
    "external-controller-pipe",
    "external-doh-server",
    "secret",
)


def _load(text: str, label: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise HostError("invalid_config", label + str(error).replace("\n", " ")) from error


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: mappings merge key by key, anything else is replaced."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_override(
    document: dict[str, Any], override: dict[str, Any], label: str
) -> dict[str, Any]:
    """One override: the deep merge first, then the insert keys on its result."""
    result = merge(document, {k: v for k, v in override.items() if k not in INSERT_KEYS})
    for key, (target, front) in INSERT_KEYS.items():
        if key not in override:
            continue
        items, current = override[key], result.get(target, [])
        if not isinstance(items, list) or not isinstance(current, list):
            raise HostError("invalid_config", f"{label}{key} and {target} must be lists")
        result[target] = [*items, *current] if front else [*current, *items]
    return result


def check_config(config: TunnelConfig) -> dict[str, Any]:
    """The simulated pipeline (architecture §3.2, ui §4): parse the config, or compose one,
    apply the overrides in order, then clear the controller keys. As in mihomo, every proxy
    a group or a rule names must exist; a config without proxies routes everything DIRECT
    and is fine. Failures use the service's error code and mihomo's wording."""
    if config.composition is not None:
        document, _ = compose(config.composition)
    else:
        document = _load(config.config_yaml, "")
    if not isinstance(document, dict):
        raise HostError("invalid_config", "the config is not a YAML mapping")
    for number, text in enumerate(config.overrides, 1):
        label = f"override {number}: "
        override = _load(text, label)
        if override is None:
            continue
        if not isinstance(override, dict):
            raise HostError("invalid_config", label + "not a YAML mapping")
        document = apply_override(document, override, label)
    for key in CONTROLLER_KEYS:
        document.pop(key, None)
    _check_references(document)
    return document


BUILT_IN_PROXIES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
# Rules whose payload holds other rules; the simulation does not look inside them.
NESTED_RULES = {"AND", "OR", "NOT", "SUB-RULE"}


def _names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item["name"]) for item in items if isinstance(item, dict) and "name" in item]


def _check_references(document: dict[str, Any]) -> None:
    groups = document.get("proxy-groups") or []
    known = BUILT_IN_PROXIES | set(_names(document.get("proxies"))) | set(_names(groups))
    for index, group in enumerate(groups if isinstance(groups, list) else []):
        members = group.get("proxies") if isinstance(group, dict) else None
        for member in members or []:
            if str(member) not in known:
                message = f"proxy group[{index}]: {group.get('name')}: '{member}' not found"
                raise HostError("invalid_config", message)
    rules = document.get("rules") or []
    for index, rule in enumerate(rules if isinstance(rules, list) else []):
        parts = [part.strip() for part in str(rule).split(",")]
        if parts[0] in NESTED_RULES:
            continue
        position = 1 if parts[0] == "MATCH" else 2
        target = parts[position] if len(parts) > position else ""
        if target not in known:
            message = f"rules[{index}] [{rule}] error: proxy [{target}] not found"
            raise HostError("invalid_config", message)


# A few rows of the core's region table (src/core/internal/compose/regions.go), enough for
# the samples; the core's own table is longer and decides for real.
SIMULATED_REGIONS = (
    ("HK", "🇭🇰", "香港", r"🇭🇰|香港|(?i:hong ?kong)|(?<![A-Za-z])HK(?![A-Za-z])"),
    ("TW", "🇹🇼", "台湾", r"🇹🇼|台湾|臺灣|(?i:taiwan)|(?<![A-Za-z])TW(?![A-Za-z])"),
    ("JP", "🇯🇵", "日本", r"🇯🇵|日本|东京|大阪|(?i:japan|tokyo)|(?<![A-Za-z])JP(?![A-Za-z])"),
    ("SG", "🇸🇬", "新加坡", r"🇸🇬|新加坡|(?i:singapore)|(?<![A-Za-z])SG(?![A-Za-z])"),
    ("US", "🇺🇸", "美国", r"🇺🇸|美国|(?i:united ?states)|(?<![A-Za-z])US(?![A-Za-z])"),
)
OTHER_REGION = "ZZ"
# Proxy types as mihomo's adapters name them (Proxy.type).
ADAPTER_TYPES = {
    "ss": "Shadowsocks",
    "vmess": "Vmess",
    "vless": "Vless",
    "trojan": "Trojan",
    "hysteria2": "Hysteria2",
    "http": "Http",
    "socks5": "Socks5",
    "direct": "Direct",
    "tailscale": "Tailscale",
    "zerotier": "ZeroTier",
    "easytier": "EasyTier",
}
# Outbounds into the user's own networks (docs/mesh.md §3): kept out of the pool.
MESH_TYPES = ("tailscale", "zerotier", "easytier")
WARP_TARGET = "egress:warp"
NETWORK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
WARP_NAMES = {"zh": "WARP 出口", "en": "WARP exit"}  # the outbound, by the plan's locale
VPNGATE_TARGET = "egress:vpngate"
VPNGATE_NAMES = {"zh": "VPN Gate 出口", "en": "VPN Gate exit"}  # the group, by locale


def _vpngate_server(
    host: str, country: str, last: int, score: int, ping: int, operator: str
) -> VpnGateServer:
    address = f"203.0.113.{last}"
    pem = "-----BEGIN CERTIFICATE-----\nc2ltdWxhdGVk\n-----END CERTIFICATE-----\n"
    options = OpenVpnOptions(
        address, 443, "tcp", "AES-128-CBC", ("AES-128-CBC",), "SHA1", "", pem, pem, pem, "", ""
    )
    return VpnGateServer(
        host, address, country, score, ping, 80_000_000, 12, 86_400_000, operator, options
    )


# VPN Gate's list as list_vpngate answers it, best score first; made up (docs/exits.md §5.2).
VPNGATE_SERVERS = (
    _vpngate_server("vpn381472650", "JP", 11, 1_480_212, 9, "DESKTOP-K7Q2's owner"),
    _vpngate_server("vpn902215377", "KR", 21, 1_204_877, 31, "MainPC's owner"),
    _vpngate_server("vpn517730941", "JP", 12, 998_120, 14, "SAKURA's owner"),
    _vpngate_server("vpn204561388", "JP", 13, 845_301, 18, "yuno's owner"),
    _vpngate_server("vpn660918245", "KR", 22, 702_455, 35, "Eunho's owner"),
    _vpngate_server("vpn731004562", "JP", 14, 655_090, 22, "GamePlayPC's owner"),
    _vpngate_server("vpn118274093", "JP", 15, 431_786, 25, "tosi's owner"),
    _vpngate_server("vpn845302117", "RU", 31, 302_115, 160, "Viktor's owner"),
    _vpngate_server("vpn390017264", "JP", 16, 255_901, 28, "ikeda's owner"),
    _vpngate_server("vpn572283910", "US", 41, 120_334, 140, "PC's owner"),
)
# Share link schemes as proxy types.
LINK_SCHEMES = {
    "ss": "ss",
    "vmess": "vmess",
    "vless": "vless",
    "trojan": "trojan",
    "hy2": "hysteria2",
}


def region_of(name: str) -> str:
    for code, _, _, pattern in SIMULATED_REGIONS:
        if re.search(pattern, name):
            return code
    return OTHER_REGION


def _share_links(text: str) -> list[dict[str, Any]]:
    with contextlib.suppress(binascii.Error, UnicodeDecodeError):
        text = base64.b64decode(text.strip() + "=" * (-len(text.strip()) % 4)).decode()
    proxies = []
    for line in text.splitlines():
        link = urlsplit(line.strip())
        if link.scheme in LINK_SCHEMES and link.fragment:
            proxies.append({"name": unquote(link.fragment), "type": LINK_SCHEMES[link.scheme]})
    return proxies


def _subscription(text: str, label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The named proxies of a subscription, each name once, and the profile itself."""
    document = _load(text, label)
    if isinstance(document, dict):
        proxies = document.get("proxies") or []
        if not isinstance(proxies, list):
            raise HostError("invalid_config", label + "proxies must be a list")
    else:
        document, proxies = {}, _share_links(text)
        if not proxies:
            raise HostError("invalid_config", label + "neither a mihomo profile nor share links")
    named: dict[str, dict[str, Any]] = {}
    for proxy in proxies:
        if isinstance(proxy, dict) and isinstance(proxy.get("name"), str) and proxy["name"]:
            named.setdefault(proxy["name"], proxy)
    return list(named.values()), document


def _info(pattern: str) -> re.Pattern[str] | None:
    """The info-node expression; one the core cannot use is an invalid configuration."""
    try:
        return re.compile(pattern) if pattern else None
    except re.error as error:
        raise HostError("invalid_config", f"info filter: {error}") from error


def inspect(text: str, info_filter: str = "") -> SourceInspection:
    info = _info(info_filter)
    proxies, document = _subscription(text, "")
    rules = document.get("rules") or []
    return SourceInspection(
        nodes=tuple(
            SourceNode(
                p["name"],
                ADAPTER_TYPES.get(str(p.get("type")), str(p.get("type"))),
                region_of(p["name"]),
                info is not None and info.search(p["name"]) is not None,
                str(p.get("type")) in MESH_TYPES,
            )
            for p in proxies
        ),
        has_rules=bool(rules),
        group_count=len(document.get("proxy-groups") or []),
        rule_count=len(rules),
    )


def _plan(composition: Composition) -> dict[str, Any]:
    try:
        plan = json.loads(composition.plan_json)
    except json.JSONDecodeError as error:
        raise HostError("invalid_config", f"plan: {error}") from error
    if not isinstance(plan, dict) or plan.get("version") != 1:
        version = plan.get("version") if isinstance(plan, dict) else None
        message = f"plan: plan version {version} is not supported; this core reads version 1"
        raise HostError("invalid_config", message)
    return plan


def _name_match(condition: Any, label: str) -> re.Pattern[str] | None:
    """A name condition as Python reads it; the core uses mihomo's regexp2."""
    if not condition:
        return None
    pattern = str(condition.get("pattern", ""))
    if condition.get("mode") == "keyword":
        return re.compile(re.escape(pattern), re.IGNORECASE)
    try:
        return re.compile(pattern)
    except re.error as error:
        raise HostError("invalid_config", f"plan: group {label!r}: {error}") from error


def _members(
    group: dict[str, Any], nodes: list[PoolNode], groups: list[ComposedGroup]
) -> tuple[list[PoolNode], list[str]]:
    """A group's members by routing.md §4.5: regions, sources and the name add up, the
    exclusion takes away, picked nodes and other members always join."""
    members = group.get("members") or {}
    name = _name_match(members.get("name"), group["id"])
    exclude = _name_match(members.get("exclude"), group["id"])
    picked = {(n.get("source"), n.get("name")) for n in members.get("nodes") or ()}
    chosen = [
        node
        for node in nodes
        if (
            (
                members.get("all")
                or node.region in (members.get("regions") or ())
                or node.source_id in (members.get("sources") or ())
                or (name is not None and name.search(node.original_name))
            )
            and not (exclude is not None and exclude.search(node.original_name))
        )
        or (node.source_id, node.original_name) in picked
    ]
    others: list[str] = []
    for other in members.get("others") or ():
        ids = [g.id for g in groups if g.kind == "region"] if other == "region:*" else [other]
        for found in ids:
            known = found in BUILT_IN_PROXIES or any(g.id == found for g in groups)
            if known and found not in others and found != group["id"]:
                others.append(found)
    return chosen, others


def compose(composition: Composition) -> tuple[dict[str, Any], CompositionReport]:
    """A small stand-in for the core's composition (docs/routing.md §8): the pool, the
    built-in and region groups, custom groups with their members, and the rules with their
    entry ids. Mesh outbounds stay out of the pool (docs/mesh.md §3); the WARP exit goes out
    through its via group, and without an account its rules reject (docs/exits.md §3.3)."""
    plan = _plan(composition)
    info = _info(composition.info_filter)
    nodes: list[PoolNode] = []
    proxies: list[dict[str, Any]] = []
    mesh: list[MeshOutbound] = []
    mesh_proxies: list[dict[str, Any]] = []
    taken = set(BUILT_IN_PROXIES)
    # The networks the user added take their names before the nodes (docs/mesh.md §10.3);
    # the custom groups and the WARP exit before them.
    taken |= {str(g.get("name")) for g in plan.get("groups") or [] if g.get("kind") == "custom"}
    taken |= {"节点选择", "自动选择", *WARP_NAMES.values()}
    vias: dict[str, str] = {}
    for network in composition.mesh:
        outbound = _network_outbound(network)
        kind = ADAPTER_TYPES[network.kind]
        name = network.name
        if name in taken:
            name = f"{network.name} · {kind}"
        taken.add(name)
        mesh.append(MeshOutbound(f"mesh:{network.id}", name, kind, ""))
        mesh_proxies.append({**outbound, "name": name})
        if network.via:
            vias[name] = network.via
    for source in composition.sources:
        for proxy in _subscription(source.yaml, f"subscription {source.name!r}: ")[0]:
            original = proxy["name"]
            kind = ADAPTER_TYPES.get(str(proxy.get("type")), str(proxy.get("type")))
            if str(proxy.get("type")) in MESH_TYPES:
                name = f"{original} · {source.name}" if original in taken else original
                taken.add(name)
                mesh.append(MeshOutbound(f"mesh:{source.id}/{original}", name, kind, source.id))
                mesh_proxies.append({**proxy, "name": name})
                continue
            if info is not None and info.search(original):
                continue
            name = f"{original} · {source.name}" if original in taken else original
            taken.add(name)
            nodes.append(PoolNode(name, original, source.id, kind, region_of(original)))
            proxies.append({**proxy, "name": name})
    proxy_name, auto_name = "节点选择", "自动选择"
    groups = [ComposedGroup("proxy", proxy_name, "builtin", "", 0)]
    groups.append(ComposedGroup("auto", auto_name, "builtin", "", len(nodes)))
    for code, flag, name, _ in (*SIMULATED_REGIONS, (OTHER_REGION, "🌐", "其他地区", "")):
        count = sum(node.region == code for node in nodes)
        if count:
            groups.append(ComposedGroup(f"region:{code}", f"{flag} {name}", "region", code, count))
    custom = [g for g in plan.get("groups") or [] if g.get("kind") == "custom"]
    groups.extend(ComposedGroup(g["id"], g.get("name", ""), "custom", "", 0) for g in custom)
    members = {g["id"]: _members(g, nodes, groups) for g in custom}
    groups = [
        replace(g, node_count=len(members[g.id][0])) if g.id in members else g for g in groups
    ]
    names = {group.id: group.name for group in groups}
    meshes = {outbound.id: outbound.name for outbound in mesh}
    warp_name = WARP_NAMES.get(str(plan.get("locale")), WARP_NAMES["zh"])
    warp_used = False
    vpngate_name = VPNGATE_NAMES.get(str(plan.get("locale")), VPNGATE_NAMES["zh"])
    vpngate_used = False
    warnings: list[CompositionWarning] = []
    for entry, proxy in zip(mesh, mesh_proxies, strict=False):
        via = vias.get(proxy["name"])
        if via is None:
            continue
        if via not in names or members.get(via, ([None], [None])) == ([], []):
            warnings.append(CompositionWarning(entry.id, "via_empty", via))
            via = "proxy"
        proxy["dialer-proxy"] = names[via]

    def target(entry_id: str, wanted: str) -> str:
        nonlocal warp_used, vpngate_used
        if wanted in BUILT_IN_PROXIES:
            return wanted
        if wanted == VPNGATE_TARGET:
            if composition.egress.vpngate:
                vpngate_used = True
                return vpngate_name
            warnings.append(CompositionWarning(entry_id, "egress_unavailable", wanted))
            return "REJECT"
        if wanted == WARP_TARGET or wanted.startswith("mesh:"):
            if wanted == WARP_TARGET and composition.egress.warp is not None:
                warp_used = True
                return warp_name
            if wanted in meshes:
                return meshes[wanted]
            reason = "egress_unavailable" if wanted == WARP_TARGET else "mesh_missing"
            warnings.append(CompositionWarning(entry_id, reason, wanted))
            return "REJECT"
        if wanted in names and members.get(wanted, ([None], [None])) != ([], []):
            return names[wanted]
        reason = "region_empty" if wanted.startswith("region:") else "group_empty"
        warnings.append(CompositionWarning(entry_id, reason, wanted))
        return proxy_name

    rules: list[Rule] = []
    for entry in plan.get("rules") or []:
        if entry.get("enabled", True):
            to = target(entry["id"], entry["target"])
            rules.extend(
                Rule(i["type"].upper(), i["value"], to, entry["id"]) for i in entry["items"]
            )
    rules.append(Rule("MATCH", "", target("fallback", plan.get("fallback") or "proxy"), "fallback"))
    account = composition.egress.warp
    if warp_used and account is not None:
        via = str(((plan.get("egress") or {}).get("warp") or {}).get("via") or "proxy")
        if via not in names or members.get(via, ([None], [None])) == ([], []):
            warnings.append(CompositionWarning(WARP_TARGET, "via_empty", via))
            via = "proxy"
        wireguard = account.protocol == "wireguard"
        host, _, port = account.endpoint.rpartition(":")
        outbound: dict[str, Any] = {
            "name": warp_name,
            "type": "wireguard" if wireguard else "masque",
            "server": host,
            "port": int(port),
            "dialer-proxy": names[via],
        }
        if account.protocol == "masque-h2":
            outbound["network"] = "h2"
        mesh_proxies.append(outbound)
    exit_groups: list[dict[str, Any]] = []
    if vpngate_used:
        settings = (plan.get("egress") or {}).get("vpngate") or {}
        via = str(settings.get("via") or "proxy")
        if via not in names or members.get(via, ([None], [None])) == ([], []):
            warnings.append(CompositionWarning(VPNGATE_TARGET, "via_empty", via))
            via = "proxy"
        servers = composition.egress.vpngate
        for server in servers:
            mesh_proxies.append(
                {
                    "name": f"VPN Gate {server.country} {server.host_name}",
                    "type": "openvpn",
                    "server": server.openvpn.server,
                    "port": server.openvpn.port,
                    "dialer-proxy": names[via],
                }
            )
        single = settings.get("mode") == "single"
        exit_groups.append(
            {
                "name": vpngate_name,
                "type": "fallback" if single else "load-balance",
                "proxies": [f"VPN Gate {s.country} {s.host_name}" for s in servers],
            }
        )

    def group_proxies(group: ComposedGroup) -> list[str]:
        if group.id not in members:
            return [p["name"] for p in proxies] or ["DIRECT"]
        chosen, others = members[group.id]
        listed = [names.get(other, other) for other in others] + [n.name for n in chosen]
        return listed or ["COMPATIBLE"]

    document = {
        "proxies": proxies + mesh_proxies,
        "proxy-groups": [
            {"name": g.name, "type": "select", "proxies": group_proxies(g)} for g in groups
        ]
        + exit_groups,
        "rules": [",".join(part for part in (r.type, r.payload, r.proxy) if part) for r in rules],
    }
    report = CompositionReport(
        tuple(warnings), tuple(groups), tuple(rules), tuple(nodes), tuple(mesh)
    )
    return document, report


def preview(composition: Composition, group_id: str) -> GroupPreview:
    """The members compose gives the group; a region without nodes has none."""
    _, report = compose(composition)
    plan = _plan(composition)
    nodes, groups = list(report.nodes), list(report.groups)
    found = next((g for g in groups if g.id == group_id), None)
    if found is None:
        if group_id.startswith("region:"):
            return GroupPreview((), ())
        raise HostError("invalid_config", f"plan: no group {group_id!r}")
    if found.kind == "region":
        return GroupPreview(tuple(n for n in nodes if n.region == found.region), ())
    planned = next((g for g in plan.get("groups") or [] if g.get("id") == group_id), None)
    if planned is None:
        default = {"proxy": {"others": ["auto", "region:*", "DIRECT"]}, "auto": {"all": True}}
        planned = {"id": group_id, "members": default[group_id]}
    chosen, others = _members(planned, nodes, groups)
    return GroupPreview(tuple(chosen), tuple(others))


# A few categories of the GEO data, enough for the rule test; the core reads the real data.
SIMULATED_GEOSITE = {
    "telegram": ("telegram.org", "t.me"),
    "cn": ("baidu.com", "qq.com", "bilibili.com"),
    "category-ads-all": ("doubleclick.net",),
    "openai": ("openai.com", "chatgpt.com"),
    "anthropic": ("anthropic.com", "claude.ai"),
    "google-gemini": ("gemini.google.com",),
    "google": ("google.com",),
    "youtube": ("youtube.com",),
    "netflix": ("netflix.com",),
    "github": ("github.com",),
    "private": ("localhost", "lan"),
}
SIMULATED_GEOIP = {
    "private": ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"),
    "telegram": ("91.108.4.0/22",),
    "cn": ("1.2.4.0/24",),
}


def _suffix(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _in_ranges(address: str, ranges: Iterable[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(cidr, strict=False) for cidr in ranges)


def _matches(kind: str, payload: str, host: str, address: str, port: int, network: str) -> bool:
    payload = payload.lower()
    match kind:
        case "DOMAIN":
            return host == payload
        case "DOMAIN-SUFFIX":
            return bool(host) and _suffix(host, payload)
        case "DOMAIN-KEYWORD":
            return bool(host) and payload in host
        case "GEOSITE":
            return bool(host) and any(_suffix(host, d) for d in SIMULATED_GEOSITE.get(payload, ()))
        case "IP-CIDR" | "IP-CIDR6":
            return _in_ranges(address, (payload,))
        case "GEOIP":
            return _in_ranges(address, SIMULATED_GEOIP.get(payload, ()))
        case "DST-PORT":
            low, _, high = payload.partition("-")
            return int(low) <= port <= int(high or low)
        case "NETWORK":
            return payload == network
        case "MATCH":
            return True
    return False


def match(
    lines: Iterable[str], entries: dict[str, str], host: str, port: int, network: str
) -> tuple[int, Rule] | None:
    """The first rule line that matches; mihomo's hosts table has localhost."""
    host = host.strip().lower().rstrip(".")
    try:
        address, host = str(ipaddress.ip_address(host)), ""
    except ValueError:
        address = "127.0.0.1" if host == "localhost" else ""
    for index, line in enumerate(lines):
        parts = [part.strip() for part in str(line).split(",")]
        if parts[-1] == "no-resolve":
            parts.pop()
        kind = parts[0].upper()
        payload, target = ("", parts[1]) if kind == "MATCH" else (",".join(parts[1:-1]), parts[-1])
        if _matches(kind, payload, host, address, port or 443, network):
            return index, Rule(kind, payload, target, entries.get(str(line), ""))
    return None


BUILTIN_ALIASES = {"节点选择": "proxy", "Proxy": "proxy", "自动选择": "auto", "Auto": "auto"}
CONVERTED_KINDS = {
    "DOMAIN": "domain",
    "DOMAIN-SUFFIX": "domain",
    "DOMAIN-KEYWORD": "domain",
    "IP-CIDR": "ip",
    "IP-CIDR6": "ip",
    "GEOSITE": "category",
    "GEOIP": "category",
}


def convert(text: str, source_id: str = "", locale: str = "zh") -> PlanConversion:
    """A stand-in for the core's conversion (routing.md §4.7): groups by name, rules by
    kind with neighbouring domains and ranges of one target together, RULE-SET and SUB-RULE
    skipped."""
    proxies, document = _subscription(text, "")
    groups = [
        g for g in document.get("proxy-groups") or [] if isinstance(g, dict) and g.get("name")
    ]
    lines = [str(line) for line in document.get("rules") or []]
    if not groups and not lines:
        raise HostError("invalid_config", "it has neither groups nor rules")
    nodes = {p["name"] for p in proxies}
    ids = {g["name"]: BUILTIN_ALIASES.get(g["name"], f"g-{n:012x}") for n, g in enumerate(groups)}
    planned: list[dict[str, Any]] = []
    for group in groups:
        group_id = ids[group["name"]]
        if group_id in ("proxy", "auto"):
            continue
        members: dict[str, Any] = {}
        for item in group.get("proxies") or ():
            if item in BUILT_IN_PROXIES or (item in ids and ids[item] != group_id):
                members.setdefault("others", []).append(ids.get(item, item))
            elif item in nodes and source_id:
                members.setdefault("nodes", []).append({"source": source_id, "name": item})
        kind = group.get("type") if group.get("type") in GROUP_TYPES else "select"
        planned.append(
            {
                "id": group_id,
                "kind": "custom",
                "name": group["name"],
                "type": kind,
                "members": members,
            }
        )
    rules: list[dict[str, Any]] = []
    skipped: list[SkippedRule] = []
    raw = 0
    fallback = "proxy"
    for number, line in enumerate(lines):
        parts = [part.strip() for part in line.split(",")]
        kind = parts[0].upper()
        if kind in ("RULE-SET", "SUB-RULE"):
            skipped.append(SkippedRule(line, "rule_set" if kind == "RULE-SET" else "sub_rule"))
            continue
        target = parts[1] if kind == "MATCH" else parts[2] if len(parts) > 2 else ""
        target = ids.get(target, target)
        if target not in BUILT_IN_PROXIES and target not in ids.values():
            skipped.append(SkippedRule(line, "target"))
            continue
        if kind == "MATCH":
            fallback = target
            skipped.extend(SkippedRule(rest, "after_match") for rest in lines[number + 1 :])
            break
        item: dict[str, Any] = {"type": kind, "value": parts[1]}
        if parts[-1] == "no-resolve":
            item["no_resolve"] = True
        entry_kind = CONVERTED_KINDS.get(kind, "raw")
        raw += entry_kind == "raw"
        last = rules[-1] if rules else None
        if (
            last is not None
            and entry_kind in ("domain", "ip")
            and last["kind"] == entry_kind
            and last["target"] == target
        ):
            last["items"].append(item)
            continue
        rules.append(
            {"id": f"r-{number:012x}", "kind": entry_kind, "items": [item], "target": target}
        )
    plan = {"version": 1, "locale": locale, "groups": planned, "rules": rules, "fallback": fallback}
    return PlanConversion(
        plan_json=json.dumps(plan, ensure_ascii=False),
        group_count=len(groups),
        merged_group_count=sum(ids[g["name"]] in ("proxy", "auto") for g in groups),
        rule_count=len(lines),
        raw_rule_count=raw,
        entry_count=len(rules),
        skipped=tuple(skipped),
    )


def export_yaml(config: TunnelConfig, kind: str) -> str:
    """The plan's groups and rules, or the whole configuration with nodes reduced to what
    tells them apart."""
    assert config.composition is not None
    if kind == EXPORT_GENERATED:
        document = check_config(config)
        document["proxies"] = [
            {key: node[key] for key in ("name", "type", "server", "port") if key in node}
            for node in document.get("proxies") or ()
        ]
    else:
        composed, _ = compose(config.composition)
        document = {"proxy-groups": composed["proxy-groups"], "rules": composed["rules"]}
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


# Each type with the fields mihomo requires of it, so the real core accepts the sample too
# (tests/e2e checks that it does).
_CORE_TYPES = {
    "VLESS": "type: vless, uuid: 2b4e5d0c-6c41-4b1e-9d5a-0a6f2f3c9e11",
    "Hysteria2": "type: hysteria2, password: sample",
    "Trojan": "type: trojan, password: sample",
    "Shadowsocks": "type: ss, cipher: aes-128-gcm, password: sample",
}
SAMPLE_CONFIG = (
    "mixed-port: 7890\nmode: rule\nproxies:\n"
    + "".join(
        f"  - {{name: {name}, {_CORE_TYPES[kind]}, server: node{i}.example.net, port: 443}}\n"
        for i, (name, (kind, _)) in enumerate(NODES.items())
    )
    + "proxy-groups:\n  - name: 节点选择\n    type: select\n    proxies:\n"
    + "".join(f"      - {name}\n" for name in NODES)
    + "rules:\n  - MATCH,节点选择\n"
)
SAMPLE_USAGE = Usage(
    upload=int(1.2 * GB),
    download=int(37.0 * GB),
    total=200 * GB,
    expire=int(datetime(2026, 12, 31).timestamp()),
)


@dataclass
class _Group:
    type: str
    members: list[str]
    now: str


@dataclass
class _Connection:
    id: str
    host: str  # empty for a connection to an IP
    address: str  # the destination IP
    port: int
    network: str  # "tcp" or "udp"
    rule: str
    target: str  # a group name or "DIRECT"
    process: str
    share: float  # the typical fraction of the traffic this connection carries
    started_at: float
    ends_at: float | None  # None until something closes it
    source_port: int
    upload: int = 0
    download: int = 0
    rate_share: float = 0.0  # this second's fraction, a walk around ``share``


# The prototype's connections, open when the host starts and until something closes them:
# host, address, port, network, rule, target, process, share, seconds since they opened.
LASTING = (
    (
        "www.youtube.com",
        "198.18.0.21",
        443,
        "udp",
        "GEOSITE,youtube",
        "视频服务",
        "Google Chrome",
        0.62,
        767,
    ),
    (
        "registry.npmjs.org",
        "198.18.0.34",
        443,
        "tcp",
        "DOMAIN-SUFFIX,npmjs.org",
        "节点选择",
        "node",
        0.12,
        38,
    ),
    (
        "github.com",
        "198.18.0.12",
        443,
        "tcp",
        "DOMAIN-SUFFIX,github.com",
        "节点选择",
        "Code Helper",
        0.04,
        252,
    ),
    ("www.wikipedia.org", "198.18.0.40", 443, "tcp", "MATCH", "节点选择", "Firefox", 0.02, 210),
    (
        "cdn.jsdelivr.net",
        "198.18.0.27",
        443,
        "tcp",
        "MATCH",
        "节点选择",
        "Google Chrome",
        0.01,
        139,
    ),
    ("www.baidu.com", "198.18.0.52", 443, "tcp", "GEOSITE,cn", "DIRECT", "Safari", 0.002, 51),
    (
        "pypi.org",
        "198.18.0.63",
        443,
        "tcp",
        "DOMAIN-SUFFIX,pypi.org",
        "节点选择",
        "python3.14",
        0.0003,
        65,
    ),
    ("", "223.5.5.5", 53, "udp", "IP-CIDR,223.5.5.5/32", "DIRECT", "mDNSResponder", 0.00001, 9),
)
# Short visits that open now and then: the same fields, then how long they stay (seconds).
VISITS = (
    (
        "www.google.com",
        "198.18.0.71",
        443,
        "tcp",
        "DOMAIN-SUFFIX,google.com",
        "节点选择",
        "Google Chrome",
        0.01,
        (5, 40),
    ),
    (
        "fonts.gstatic.com",
        "198.18.0.72",
        443,
        "tcp",
        "DOMAIN-SUFFIX,gstatic.com",
        "节点选择",
        "Google Chrome",
        0.004,
        (3, 20),
    ),
    (
        "rr3---sn-i3b7kn7s.googlevideo.com",
        "198.18.0.73",
        443,
        "udp",
        "GEOSITE,youtube",
        "视频服务",
        "Google Chrome",
        0.25,
        (8, 60),
    ),
    (
        "api.github.com",
        "198.18.0.74",
        443,
        "tcp",
        "DOMAIN-SUFFIX,github.com",
        "节点选择",
        "Code Helper",
        0.003,
        (2, 15),
    ),
    (
        "objects.githubusercontent.com",
        "198.18.0.75",
        443,
        "tcp",
        "DOMAIN-SUFFIX,githubusercontent.com",
        "节点选择",
        "git-remote-https",
        0.08,
        (4, 25),
    ),
    (
        "files.pythonhosted.org",
        "198.18.0.76",
        443,
        "tcp",
        "DOMAIN-SUFFIX,pythonhosted.org",
        "节点选择",
        "uv",
        0.05,
        (3, 20),
    ),
    (
        "www.bilibili.com",
        "198.18.0.77",
        443,
        "tcp",
        "GEOSITE,cn",
        "DIRECT",
        "Safari",
        0.01,
        (5, 30),
    ),
    (
        "update.code.visualstudio.com",
        "198.18.0.78",
        443,
        "tcp",
        "DOMAIN-SUFFIX,visualstudio.com",
        "节点选择",
        "Code Helper",
        0.002,
        (2, 10),
    ),
    (
        "time.apple.com",
        "198.18.0.79",
        123,
        "udp",
        "DOMAIN-SUFFIX,apple.com",
        "DIRECT",
        "timed",
        0.00001,
        (1, 3),
    ),
    (
        "",
        "223.5.5.5",
        53,
        "udp",
        "IP-CIDR,223.5.5.5/32",
        "DIRECT",
        "mDNSResponder",
        0.00001,
        (1, 4),
    ),
    ("api.openai.com", "198.18.0.80", 443, "tcp", "MATCH", "节点选择", "curl", 0.006, (2, 12)),
)
VISITS_PER_S = 0.6
MAX_CONNECTIONS = 40  # besides the crowd
# How mihomo names rule types in its log lines.
RULE_NAMES = {
    "DOMAIN-SUFFIX": "DomainSuffix",
    "GEOSITE": "GeoSite",
    "IP-CIDR": "IPCIDR",
    "MATCH": "Match",
}
STARTUP_LOG = (
    ("info", "Start initial configuration in progress"),
    ("info", "Initial configuration complete, total time: 48ms"),
    ("info", "Sniffer is loaded and working"),
    ("info", "Mixed(http+socks) proxy listening at: 127.0.0.1:7890"),
    (
        "info",
        "[TUN] Tun adapter listening at: utun4([198.18.0.1/30],[]), mtu: 9000, auto route: true, auto redir: false, ip stack: Mixed",
    ),
)
# Lines that turn up now and then: level, text, how often per second.
OCCASIONAL_LOG = (
    ("info", "[URLTest] 自动选择 selected 香港 02 (54ms)", 1 / 30),
    ("warning", "[URLTest] 美国 02 delay 412ms exceeds tolerance", 1 / 40),
    (
        "warning",
        "[TCP] dial 节点选择 (match DomainSuffix/example.net) 198.18.0.1:52390 --> example.net:443 error: 新加坡 02 i/o timeout",
        1 / 25,
    ),
    ("error", "[Provider] rule-set geosite-cn pull error: 403 Forbidden", 1 / 60),
)
LOG_BUFFER = 1024  # the service's buffer for one log subscription


def _network_outbound(network: MeshNetwork) -> dict[str, Any]:
    """The network as the core writes it (docs/mesh.md §10.3), with the core's checks."""
    label = f"mesh network {network.name!r}: "
    if not NETWORK_ID.fullmatch(network.id):
        raise HostError("invalid_config", label + f"the id {network.id!r} is not letters, digits")
    if not network.name.strip() or "," in network.name:
        raise HostError("invalid_config", label + "the name is empty or holds a comma")
    outbound: dict[str, Any] = {"type": network.kind, "state-dir": f"mesh/{network.id}"}
    match network.settings:
        case TailscaleSettings() as settings:
            if settings.control_url and not settings.control_url.startswith(
                ("https://", "http://")
            ):
                raise HostError("invalid_config", label + "the control server is no http URL")
            outbound |= {"auth-key": settings.auth_key, "control-url": settings.control_url}
        case ZeroTierSettings() as settings:
            if not re.fullmatch(r"[0-9A-Fa-f]{16}", settings.network):
                raise HostError("invalid_config", label + "the network id is not 16 hex digits")
            outbound["network"] = settings.network.lower()
        case EasyTierSettings() as settings:
            if not settings.network_name or not settings.network_secret or not settings.peers:
                raise HostError("invalid_config", label + "the network needs a name, secret, peers")
            outbound |= {"network-name": settings.network_name, "peers": list(settings.peers)}
    return {k: v for k, v in outbound.items() if v != ""}


def _rank(level: str) -> int:
    return LOG_LEVELS.index(level)


class SimulatedHost:
    def __init__(
        self,
        *,
        seed: int = 7,
        clock: Callable[[], float] = time.time,
        health_check_s: float = 0.0,
        crowd: int = 0,
        development: bool = False,
        core_needs_tunnel: bool = False,
        takeover: str = TAKEOVER_SETTINGS,
    ) -> None:
        """``crowd`` adds that many quiet connections, to try the UI with a busy core.
        ``development`` refuses TUN and the system proxy as the development service
        does (docs/architecture.md §11). ``core_needs_tunnel``: like the macOS extension, no
        core answers while the tunnel is down; prepare_start brings it up. ``takeover`` is
        what connecting takes over, as on the platform's host (TunnelHost.takeover); the
        app's development service is ``development`` with TAKEOVER_NONE."""
        self.takeover = takeover
        self._random = random.Random(seed)
        # Connections and logs draw from their own generator, so traffic stays the same.
        self._churn = random.Random(seed + 1)
        self._clock = clock
        self._health_check_s = health_check_s
        self._development = development
        self.busy_ports: set[int] = set()  # mixed ports that another program listens on
        # Whether the core was built with with_gvisor, which the gvisor and mixed stacks need.
        self.gvisor = True
        # Whether the core has GEO data to list; without it the installer's list stands in.
        self.has_geodata = True
        self._running = True
        self.core_needs_tunnel = core_needs_tunnel
        self.prepared = 0  # prepare_start's calls
        self._tunnel_up = False  # prepared, Start not sent yet
        self._failed = False  # the last start could not open its port
        self.config: TunnelConfig | None = None  # the last config started or reloaded
        self.effective: dict[str, Any] | None = None  # that config after the pipeline
        self.report: CompositionReport | None = None  # its report, for a composition
        self.reloads = 0
        # register_warp's calls as (via, protocol, re-keyed device or ""), and the error the
        # next one raises instead of registering.
        self.warp_calls: list[tuple[str, str, str]] = []
        self.warp_error: HostError | None = None
        # list_vpngate's via groups, the list it answers and the error the next call raises.
        self.vpngate_calls: list[str] = []
        self.vpngate_servers: tuple[VpnGateServer, ...] = VPNGATE_SERVERS
        self.vpngate_error: HostError | None = None
        # The VPN Gate exit's servers as the proxies page lists them, by outbound name, and
        # their last delay test: None until tested, 0 when it failed.
        self._openvpn: dict[str, VpnGateServer] = {}
        self._openvpn_delays: dict[str, int | None] = {}
        # How the mesh outbounds stand, by name, in place of what the simulation makes up;
        # and the outbounds login_mesh was asked for, which are online from then on.
        self.mesh_states: dict[str, MeshStatus] = {}
        self.mesh_logins: list[str] = []
        self._mode = "rule"
        # Running as the default settings ask for.
        running = taken_over(RunOptions(not development, not development), takeover)
        self._tun = running.tun_enabled
        self._system_proxy = running.system_proxy_enabled
        self._started_at = clock() - 8076
        self._delays = {name: delay for name, (_, delay) in NODES.items()}
        self._groups = {
            "节点选择": _Group("Selector", ["自动选择", *NODES], "香港 01"),
            "自动选择": _Group("URLTest", list(NODES)[:7], "香港 02"),
            "故障转移": _Group("Fallback", ["香港 01", "日本 01", "新加坡 01"], "香港 01"),
            "视频服务": _Group(
                "Selector", ["节点选择", "香港 02", "日本 01", "美国 01"], "节点选择"
            ),
            "全球直连": _Group("Selector", ["DIRECT", "节点选择"], "DIRECT"),
        }
        self._down = 6_000_000.0
        self._up = 900_000.0
        self._down_total = int(4.8 * GB)
        self._up_total = 312 * MB
        self._last_poll = clock()
        self._last_advance = clock()
        self._visit_debt = 0.0
        self._connections: list[_Connection] = []
        for host, address, port, network, rule, target, process, share, ago in LASTING:
            connection = self._open(
                host, address, port, network, rule, target, process, share, clock() - ago, None
            )
            connection.download = int(self._down * share * ago / 2)
            connection.upload = int(self._up * share * ago / 2)
        for number in range(crowd):
            self._open(
                f"host{number}.example.com",
                f"198.18.{1 + number // 250}.{1 + number % 250}",
                443,
                "tcp" if number % 4 else "udp",
                "MATCH",
                "节点选择",
                "Google Chrome",
                0.0001,
                clock() - self._churn.uniform(1, 3600),
                None,
            )
        self._crowd = crowd
        self._logs: deque[LogEntry] = deque(maxlen=LOG_BUFFER)
        self._log_level = ""
        self._log_lines(STARTUP_LOG)
        self._log_lines(
            [
                ("debug", "[DNS] github.com --> [198.18.0.12] A from fakeip"),
                *[(level, text) for level, text, _ in OCCASIONAL_LOG[1:]],
            ]
        )

    # -- TunnelHost --------------------------------------------------------------------------
    @property
    def core_available(self) -> bool:
        return not self.core_needs_tunnel or self._running or self._tunnel_up

    def _need_core(self) -> None:
        if not self.core_available:
            raise HostError("not_running", "the tunnel is not connected")

    async def prepare_start(self, options: RunOptions) -> None:
        self.prepared += 1
        self._tunnel_up = True

    async def get_info(self) -> HostInfo:
        return HostInfo(1, "simulated", "mihomo v1.19.33", self._state(), "available")

    async def get_status(self) -> HostStatus:
        return HostStatus(
            state=self._state(),
            mode=self._mode,
            started_at_unix_ms=int(self._started_at * 1000) if self._running else 0,
            memory_bytes=48 * MB if self._running else 0,
            connection_count=len(self._connections),
            tun_enabled=self._tun,
            system_proxy_enabled=self._system_proxy,
        )

    async def validate(self, config: TunnelConfig) -> CompositionReport | None:
        self._need_core()
        check_config(config)
        return compose(config.composition)[1] if config.composition is not None else None

    async def start(self, config: TunnelConfig) -> CompositionReport | None:
        if self._running:
            raise HostError("already_running", "the core is already running")
        try:
            self._apply(config)
        except HostError as error:
            # Like the core: a port that did not open fails the start; a refused config
            # leaves it stopped.
            self._failed = error.code == "apply_failed"
            raise
        self._running = True
        self._tunnel_up = False
        self._failed = False
        self._started_at = self._clock()
        self._last_poll = self._last_advance = self._clock()
        self._log_lines(STARTUP_LOG)
        return self.report

    async def reload(self, config: TunnelConfig) -> CompositionReport | None:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        self._apply(config)
        self.reloads += 1
        self._log_lines(STARTUP_LOG[:2])
        return self.report

    async def inspect_source(self, yaml: str, info_filter: str = "") -> SourceInspection:
        self._need_core()
        return inspect(yaml, info_filter)

    async def list_geo_categories(self) -> GeoCategories:
        bundled = bundled_geo_categories()
        if not self.has_geodata or not self.core_available:
            return bundled
        at = int(self._clock() * 1000)
        return GeoCategories(
            replace(bundled.geosite, updated_at_unix_ms=at, bundled=False),
            replace(bundled.geoip, updated_at_unix_ms=at, bundled=False),
        )

    async def preview_group(self, composition: Composition, group_id: str) -> GroupPreview:
        self._need_core()
        return preview(composition, group_id)

    async def match_rule(
        self, host: str, port: int = 0, network: str = "tcp", config: TunnelConfig | None = None
    ) -> RuleMatch:
        self._need_core()
        if not host.strip() or re.search(r"[\s/,]", host.strip()):
            raise HostError("invalid_request", f"{host!r} is neither a domain nor an IP address")
        if config is None:
            if not self._running:
                raise HostError("not_running", "the core is not running")
            rules = await self.list_rules()
            lines = [",".join(p for p in (r.type, r.payload, r.proxy) if p) for r in rules]
            entries = {line: r.entry_id for line, r in zip(lines, rules, strict=True)}
        else:
            document = check_config(config)
            lines = [str(line) for line in document.get("rules") or ()]
            report = compose(config.composition)[1] if config.composition is not None else None
            entries = {}
            for rule in report.rules if report else ():
                line = ",".join(p for p in (rule.type, rule.payload, rule.proxy) if p)
                entries.setdefault(line, rule.entry_id)
        found = match(lines, entries, host, port, network)
        if found is None:
            return RuleMatch(False, 0, None, ("DIRECT",), "")
        index, rule = found
        chain = self._chains(rule.proxy) if self._running else (rule.proxy,)
        return RuleMatch(True, index, rule, chain, "")

    async def convert_to_plan(
        self, yaml: str, *, source_id: str = "", locale: str = "zh", info_filter: str = ""
    ) -> PlanConversion:
        self._need_core()
        _info(info_filter)
        return convert(yaml, source_id, locale)

    async def export_plan(self, config: TunnelConfig, export: str = EXPORT_SHARE) -> str:
        self._need_core()
        if config.composition is None:
            raise ValueError("export_plan needs a composition")
        return export_yaml(config, export)

    async def register_warp(
        self, via: str, protocol: str = "masque-h2", existing: WarpAccount | None = None
    ) -> WarpAccount:
        # A fixed made-up device; the real one comes from Cloudflare (docs/exits.md §3.2).
        if not self._running:
            raise HostError("not_running", "the core is not running")
        groups = _names((self.effective or {}).get("proxy-groups"))
        if self.effective is not None and via not in groups:
            raise HostError("invalid_request", f"no group {via!r} in the running configuration")
        self.warp_calls.append((via, protocol, existing.device_id if existing else ""))
        if self.warp_error is not None:
            error, self.warp_error = self.warp_error, None
            raise error
        return WarpAccount(
            device_id=existing.device_id if existing else "a1b2c3d4-0000-4000-8000-000000000000",
            token=existing.token if existing else "simulated-token",
            protocol=protocol,
            private_key="simulated-private-key",
            peer_public_key="simulated-peer-key",
            endpoint="162.159.198.1:443",
            address_v4="172.16.0.2",
            address_v6="2606:4700:110:8a36::2",
            client_id="AAAA",
            registered_at_unix_ms=1_793_318_400_000,
        )

    async def list_vpngate(self, via: str) -> list[VpnGateServer]:
        # A fixed made-up list; the real one comes from VPN Gate (docs/exits.md §5.2).
        if not self._running:
            raise HostError("not_running", "the core is not running")
        # Like the core, any outbound of the running configuration will do: a group, a node
        # or a built-in one such as DIRECT.
        effective = self.effective or {}
        known = BUILT_IN_PROXIES | set(_names(effective.get("proxy-groups")))
        known |= set(_names(effective.get("proxies")))
        if self.effective is not None and via not in known:
            raise HostError("invalid_request", f"no group {via!r} in the running configuration")
        self.vpngate_calls.append(via)
        if self.vpngate_error is not None:
            error, self.vpngate_error = self.vpngate_error, None
            raise error
        return list(self.vpngate_servers)

    def fail_vpngate(self, host_name: str) -> None:
        """Make the VPN Gate server's last delay test fail, as the exit's health check would."""
        name = next(n for n, s in self._openvpn.items() if s.host_name == host_name)
        self._openvpn_delays[name] = 0

    async def get_mesh_status(self) -> list[MeshStatus]:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        networks = {f"mesh:{n.id}": n for n in self._composition_mesh()}
        statuses = []
        for number, outbound in enumerate(self.report.mesh if self.report else (), 1):
            status = self.mesh_states.get(outbound.name)
            if status is None:
                status = self._mesh_status(outbound, networks.get(outbound.id), number)
            statuses.append(status)
        return statuses

    async def login_mesh(self, name: str) -> str:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        outbound = (
            next((m for m in self.report.mesh if m.name == name), None) if self.report else None
        )
        if outbound is None or outbound.type != "Tailscale":
            raise HostError("invalid_request", f"{name} has no login")
        self.mesh_logins.append(name)
        return "https://login.tailscale.com/a/simulated"

    def _composition_mesh(self) -> tuple[MeshNetwork, ...]:
        composition = self.config.composition if self.config else None
        return composition.mesh if composition else ()

    def _mesh_status(
        self, outbound: MeshOutbound, network: MeshNetwork | None, number: int
    ) -> MeshStatus:
        """Made up: Tailscale online with a key or after a login, ZeroTier waiting for its
        admin without an identity, EasyTier online with as many devices as peers."""
        status = MeshStatus(outbound.name, outbound.id, outbound.type, "online")
        settings = network.settings if network else None
        login = "https://login.tailscale.com/a/simulated"
        match settings:
            case TailscaleSettings(auth_key="") if outbound.name not in self.mesh_logins:
                return replace(status, state="needs_login", login_url=login)
            case ZeroTierSettings(identity_secret=""):
                return replace(status, state="waiting", node_id="a1b2c3d4e5")
            case EasyTierSettings():
                address = settings.ipv4 or f"10.144.144.{number + 1}/24"
                return replace(
                    status,
                    addresses=(address,),
                    peer_count=len(settings.peers),
                    network_name=settings.network_name,
                )
        if outbound.type == "ZeroTier":
            return replace(
                status,
                addresses=(f"10.147.17.{number + 4}/24",),
                peer_count=2,
                node_id="a1b2c3d4e5",
                network_name="office",
            )
        return replace(
            status,
            addresses=(f"100.101.102.{number + 2}/32",),
            peer_count=4,
            network_name="example.ts.net",
        )

    async def stop(self) -> None:
        self._running = False
        self._tunnel_up = False
        self._failed = False
        self._connections.clear()

    async def set_mode(self, mode: str) -> None:
        if mode not in ("rule", "global", "direct"):
            raise HostError("invalid_request", f"unknown mode {mode}")
        self._mode = mode

    async def set_tun(self, enabled: bool) -> None:
        self._tun = enabled

    async def set_system_proxy(self, enabled: bool) -> None:
        if enabled and self._development:
            raise HostError(
                "unsupported_method", "the development server never sets the system proxy"
            )
        self._system_proxy = enabled

    async def list_groups(self) -> list[ProxyGroup]:
        return [
            ProxyGroup(name, group.type, group.now, tuple(self._proxy(m) for m in group.members))
            for name, group in self._groups.items()
        ]

    async def list_rules(self) -> list[Rule]:
        if not self._running:
            return []
        if self.report is not None:
            return list(self.report.rules)
        # Running before any start, the sample's own rules are in use.
        effective = self.effective if self.effective is not None else _load(SAMPLE_CONFIG, "")
        rules = []
        for line in effective.get("rules") or []:
            parts = [part.strip() for part in str(line).split(",")]
            if parts[-1] == "no-resolve":
                parts.pop()
            if parts[0] == "MATCH":
                rules.append(Rule("MATCH", "", parts[1], ""))
            else:
                rules.append(Rule(parts[0], ",".join(parts[1:-1]), parts[-1], ""))
        return rules

    async def select_proxy(self, group: str, proxy: str) -> None:
        target = self._groups.get(group)
        if target is None or target.type != "Selector":
            raise HostError("invalid_request", f"{group} is not a selector group")
        if proxy not in target.members:
            raise HostError("invalid_request", f"{proxy} is not in {group}")
        target.now = proxy

    async def health_check(self, group: str) -> None:
        if group not in self._groups:
            raise HostError("invalid_request", f"unknown group {group}")
        await asyncio.sleep(self._health_check_s)
        for name in self._groups[group].members:
            if name in NODES and self._delays[name]:
                base = NODES[name][1]
                self._delays[name] = max(20, base + self._random.randint(-25, 25))
        alive = [n for n in self._groups["自动选择"].members if self._delays[n]]
        self._groups["自动选择"].now = min(alive, key=lambda n: self._delays[n])

    async def poll_traffic(self) -> list[TrafficSample]:
        if not self._running:
            return []
        now = self._clock()
        count = min(max(round(now - self._last_poll), 1), 10)
        self._last_poll = now
        samples = []
        for _ in range(count):
            self._down = min(max(self._down * self._random.uniform(0.8, 1.22), 400_000), 14_000_000)
            self._up = min(max(self._up * self._random.uniform(0.8, 1.22), 60_000), 2_000_000)
            self._down_total += int(self._down)
            self._up_total += int(self._up)
            samples.append(
                TrafficSample(int(self._up), int(self._down), self._up_total, self._down_total)
            )
        return samples

    async def poll_connections(self) -> list[Connection]:
        if not self._running:
            return []
        self._advance()
        source = "198.18.0.1" if self._tun else "127.0.0.1"
        return [
            Connection(
                id=c.id,
                host=c.host,
                network=c.network,
                rule=c.rule,
                chains=self._chains(c.target),
                process=c.process,
                upload_bytes=c.upload,
                download_bytes=c.download,
                started_at_unix_ms=int(c.started_at * 1000),
                source=f"{source}:{c.source_port}",
                destination=f"{c.address}:{c.port}",
            )
            for c in self._connections
        ]

    async def close_connection(self, connection_id: str) -> None:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        self._connections = [c for c in self._connections if c.id != connection_id]

    async def close_all_connections(self) -> None:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        self._connections.clear()

    async def poll_logs(self, level: str) -> list[LogEntry]:
        if level not in LOG_LEVELS:
            raise HostError("invalid_request", f"unknown log level {level}")
        self._advance()
        if level != self._log_level:
            first, self._log_level = not self._log_level, level
            if not first:
                # A new subscription at another level: only lines from now on.
                self._logs.clear()
                return []
        lines = [line for line in self._logs if _rank(line.level) >= _rank(level)]
        self._logs.clear()
        return lines

    async def close(self) -> None:
        pass

    # -- helpers -----------------------------------------------------------------------------
    def _open(
        self,
        host: str,
        address: str,
        port: int,
        network: str,
        rule: str,
        target: str,
        process: str,
        share: float,
        started_at: float,
        ends_at: float | None,
    ) -> _Connection:
        connection = _Connection(
            id=str(uuid.UUID(int=self._churn.getrandbits(128), version=4)),
            host=host,
            address=address,
            port=port,
            network=network,
            rule=rule,
            target=target,
            process=process,
            share=share,
            started_at=started_at,
            ends_at=ends_at,
            source_port=self._churn.randint(49152, 65535),
            rate_share=share,
        )
        self._connections.append(connection)
        return connection

    def _advance(self) -> None:
        """Move the connections and the log on to the clock's time."""
        now = self._clock()
        elapsed = max(now - self._last_advance, 0.0)
        self._last_advance = now
        if elapsed == 0:
            return
        for level, text, per_s in OCCASIONAL_LOG:
            if self._churn.random() < per_s * elapsed:
                self._log(level, text)
        if not self._running:
            return
        self._connections = [c for c in self._connections if c.ends_at is None or c.ends_at > now]
        for c in self._connections:
            c.rate_share = c.share * self._churn.uniform(0.6, 1.4)
            c.download += int(self._down * c.rate_share * elapsed)
            c.upload += int(self._up * c.rate_share * elapsed)
        self._visit_debt += elapsed * VISITS_PER_S * self._churn.uniform(0.5, 1.5)
        while self._visit_debt >= 1:
            self._visit_debt -= 1
            if len(self._connections) < MAX_CONNECTIONS + self._crowd:
                self._visit(now)

    def _visit(self, now: float) -> None:
        host, address, port, network, rule, target, process, share, (shortest, longest) = (
            self._churn.choice(VISITS)
        )
        lifetime = self._churn.uniform(shortest, longest)
        c = self._open(
            host, address, port, network, rule, target, process, share, now, now + lifetime
        )
        source = "198.18.0.1" if self._tun else "127.0.0.1"
        if host:
            self._log("debug", f"[DNS] {host} --> [{address}] A from fakeip")
        kind, _, payload = rule.partition(",")
        matched = (
            f"{RULE_NAMES.get(kind, kind)}({payload})" if payload else RULE_NAMES.get(kind, kind)
        )
        chains = self._chains(target)
        using = chains[0] if len(chains) == 1 else f"{chains[0]}[{chains[-1]}]"
        self._log(
            "info",
            f"[{network.upper()}] {source}:{c.source_port} --> {host or address}:{port} match {matched} using {using}",
        )

    def _log(self, level: str, text: str) -> None:
        # Until the first poll subscribes, every line waits for it, as if the UI had
        # subscribed just after the core started.
        if not self._log_level or _rank(level) >= _rank(self._log_level):
            self._logs.append(LogEntry(level, text, int(self._clock() * 1000)))

    def _log_lines(self, lines: Iterable[tuple[str, str]]) -> None:
        for level, text in lines:
            self._log(level, text)

    def _apply(self, config: TunnelConfig) -> None:
        config = replace(config, options=taken_over(config.options, self.takeover))
        if self._development and config.options.system_proxy_enabled:
            raise HostError(
                "unsupported_method", "the development server never sets the system proxy"
            )
        if self._development and config.options.tun_enabled:
            raise HostError("unsupported_method", "this host never creates a TUN")
        stack = config.options.tun_stack
        if stack and stack not in TUN_STACKS:
            raise HostError("invalid_request", "unknown TUN stack")
        if config.options.tun_enabled and stack in ("gvisor", "mixed") and not self.gvisor:
            message = f"tun.stack {stack} needs a build with the with_gvisor tag"
            raise HostError("invalid_config", message)
        self.effective = check_config(config)
        if stack:
            tun = self.effective.get("tun")
            self.effective["tun"] = {**(tun if isinstance(tun, dict) else {}), "stack": stack}
        report = compose(config.composition)[1] if config.composition is not None else None
        if config.options.mixed_port in self.busy_ports:
            address = f"127.0.0.1:{config.options.mixed_port}"
            message = f"listen tcp {address}: bind: address already in use"
            raise HostError("apply_failed", message, retryable=True)
        self.config = config
        self.report = report
        self._show_exit_groups()
        self._tun = config.options.tun_enabled
        self._system_proxy = config.options.system_proxy_enabled

    def _show_exit_groups(self) -> None:
        """List the VPN Gate exit's group, as the core does once a rule uses it; new servers
        start untested, as after a reload."""
        for name in VPNGATE_NAMES.values():
            self._groups.pop(name, None)
        servers = {s.host_name: s for s in VPNGATE_SERVERS + self.vpngate_servers}
        self._openvpn, self._openvpn_delays = {}, {}
        for group in (self.effective or {}).get("proxy-groups") or []:
            if group.get("name") not in VPNGATE_NAMES.values():
                continue
            kind = "Fallback" if group.get("type") == "fallback" else "LoadBalance"
            members = [str(m) for m in group.get("proxies") or []]
            for member in members:
                host = member.rsplit(" ", 1)[-1]
                if host in servers:
                    self._openvpn[member] = servers[host]
                    self._openvpn_delays[member] = None
            self._groups[group["name"]] = _Group(kind, members, members[0] if members else "")

    def _state(self) -> str:
        if self._running:
            return "running"
        return "failed" if self._failed else "stopped"

    def _proxy(self, name: str) -> Proxy:
        if name == "DIRECT":
            return Proxy("DIRECT", "Direct", None)
        if name in self._groups:
            final = self._final(name)
            return Proxy(name, self._groups[name].type, self._delays.get(final))
        if name in self._openvpn:
            return Proxy(name, "OpenVPN", self._openvpn_delays.get(name))
        return Proxy(name, NODES[name][0], self._delays[name])

    def _final(self, name: str) -> str:
        while name in self._groups:
            name = self._groups[name].now
        return name

    def _chains(self, target: str) -> tuple[str, ...]:
        if target not in self._groups:
            return (target,)
        return (target, self._final(target))


class SimulatedFetcher:
    """Serves SAMPLE_CONFIG for every link; set ``error`` to make downloads fail."""

    def __init__(self, *, delay_s: float = 0.0) -> None:
        self._delay_s = delay_s
        self.body = SAMPLE_CONFIG.encode()
        self.usage: Usage | None = SAMPLE_USAGE
        self.filename: str | None = None
        self.error: ProfileError | None = None
        self.requests: list[str] = []

    async def fetch(self, url: str) -> Download:
        self.requests.append(url)
        await asyncio.sleep(self._delay_s)
        if self.error is not None:
            raise self.error
        return Download(self.body, self.usage, self.filename)


def seed_profiles(store: ProfileStore, now: float) -> ProfileList:
    """The prototype's three profiles, with "示例订阅 A" in use: its own rules route, as the
    first import of a profile with rules decides (docs/routing.md §5.1)."""
    local = store.directory / "config.yaml"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(SAMPLE_CONFIG, encoding="utf-8")
    count = count_proxies(SAMPLE_CONFIG)
    profiles = (
        Profile(
            id="sample-a",
            name="示例订阅 A",
            kind=KIND_SUBSCRIPTION,
            url="https://sub.example.com/api/v1/client/subscribe?token=sample",
            auto_update_hours=12,
            updated_at=now - 2 * 3600,
            usage=SAMPLE_USAGE,
            proxy_count=count,
        ),
        Profile(
            id="sample-b",
            name="示例订阅 B",
            kind=KIND_SUBSCRIPTION,
            url="https://nodes.example.org/clash.yaml",
            auto_update_hours=0,  # updated by hand
            updated_at=now - 3 * 86400,
            usage=Usage(int(3 * GB), 60 * GB, 100 * GB, int(datetime(2027, 3, 1).timestamp())),
            proxy_count=count,
        ),
        Profile(
            id="sample-c",
            name="本地配置",
            kind=KIND_FILE,
            path=str(local),
            updated_at=now - 9 * 86400,
            proxy_count=count,
        ),
    )
    for profile in profiles:
        store.write_config(profile.id, SAMPLE_CONFIG)
    seeded = ProfileList(profiles, "sample-a")
    store.save(seeded)
    RoutingStore(store.database).update_state(mode=MODE_PROFILE)
    return seeded
