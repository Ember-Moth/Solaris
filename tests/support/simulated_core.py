"""What the simulated host answers in the core's place, for the UI's tests and
scripts/preview.py: the admission of a configuration and its overrides, composition,
InspectSource, PreviewGroup, the rule test, ConvertToPlan and ExportPlan.

Each is a small stand-in, enough for the samples, and the core decides for real. Where the
UI's tests rely on an answer, testdata/core-vectors.json holds the core's own, which
src/core/internal/compose/vectors_test.go keeps current and tests/test_simulated.py checks
this module against; tests/e2e compares the admission with the development service.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import ipaddress
import json
import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from solaris.host.base import (
    EXPORT_SHARE,
    ComposedGroup,
    Composition,
    CompositionReport,
    CompositionWarning,
    EasyTierSettings,
    GroupPreview,
    HostError,
    MeshNetwork,
    MeshOutbound,
    PlanConversion,
    PoolNode,
    Rule,
    SkippedRule,
    SourceInspection,
    SourceNode,
    TailscaleSettings,
    TunnelConfig,
    ZeroTierSettings,
)
from solaris.models.plans import GROUP_TYPES

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
WARP_NAMES = {"zh": "WARP 出口", "en": "WARP exit"}  # the outbound, by the plan's locale
VPNGATE_TARGET = "egress:vpngate"
VPNGATE_NAMES = {"zh": "VPN Gate 出口", "en": "VPN Gate exit"}  # the group, by locale


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


def info_filter_of(pattern: str) -> re.Pattern[str] | None:
    """The info-node expression; one the core cannot use is an invalid configuration."""
    try:
        return re.compile(pattern) if pattern else None
    except re.error as error:
        raise HostError("invalid_config", f"info filter: {error}") from error


def inspect(text: str, info_filter: str = "") -> SourceInspection:
    info = info_filter_of(info_filter)
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
    info = info_filter_of(composition.info_filter)
    nodes: list[PoolNode] = []
    proxies: list[dict[str, Any]] = []
    mesh: list[MeshOutbound] = []
    mesh_proxies: list[dict[str, Any]] = []
    taken = _reserved(plan, composition)
    # The networks the user added take their names before the nodes (docs/mesh.md §10.3).
    vias: dict[str, str] = {}
    for network in composition.mesh:
        outbound = _network_outbound(network)
        kind = ADAPTER_TYPES[network.kind]
        name = _unique(network.name, kind, taken)
        mesh.append(MeshOutbound(f"mesh:{network.id}", name, kind, ""))
        mesh_proxies.append({**outbound, "name": name})
        if network.via:
            vias[name] = network.via
    for source in composition.sources:
        for proxy in _subscription(source.yaml, f"subscription {source.name!r}: ")[0]:
            original = proxy["name"]
            kind = ADAPTER_TYPES.get(str(proxy.get("type")), str(proxy.get("type")))
            if str(proxy.get("type")) in MESH_TYPES:
                name = _unique(original, source.name, taken)
                mesh.append(MeshOutbound(f"mesh:{source.id}/{original}", name, kind, source.id))
                mesh_proxies.append({**proxy, "name": name})
                continue
            if info is not None and info.search(original):
                continue
            name = _unique(original, source.name, taken)
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
        raise HostError("invalid_config", f'plan: no group "{group_id}"')
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
    """The plan's groups and rules, or the whole configuration as the simulation checked it;
    what the core leaves out of a generated one is its own (engine's plans_test.go)."""
    assert config.composition is not None
    if kind == EXPORT_SHARE:
        composed, _ = compose(config.composition)
        document = {"proxy-groups": composed["proxy-groups"], "rules": composed["rules"]}
    else:
        document = check_config(config)
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


# The built-in groups' names by the plan's locale, as the core gives them.
BUILTIN_NAMES = {"zh": ("节点选择", "自动选择"), "en": ("Proxy", "Auto")}


def _reserved(plan: dict[str, Any], composition: Composition) -> set[str]:
    """The names the core takes before any network or node (compose.go's groupNames):
    mihomo's own, the exits' and the VPN Gate servers', the built-in groups', every region's
    whether it has nodes or not, and the custom groups'. The simulation names its groups in
    Chinese whatever the locale, so those names are taken too."""
    locale = "en" if plan.get("locale") == "en" else "zh"
    taken = set(BUILT_IN_PROXIES)
    taken |= {WARP_NAMES[locale], VPNGATE_NAMES[locale]}
    taken |= {f"VPN Gate {s.country} {s.host_name}" for s in composition.egress.vpngate}
    taken |= {*BUILTIN_NAMES[locale], *BUILTIN_NAMES["zh"]}
    taken |= {f"{flag} {name}" for _, flag, name, _ in SIMULATED_REGIONS} | {"🌐 其他地区"}
    taken |= {str(g.get("name")) for g in plan.get("groups") or [] if g.get("kind") == "custom"}
    return taken


def _unique(name: str, suffix: str, taken: set[str]) -> str:
    """The name, or with " · " and the suffix when it is taken, and a number if that is
    taken too, as the core does it; the result is taken from then on."""
    unique = name
    if unique in taken:
        unique = f"{name} · {suffix}"
        number = 2
        while unique in taken:
            unique = f"{name} · {suffix} {number}"
            number += 1
    taken.add(unique)
    return unique


def _network_outbound(network: MeshNetwork) -> dict[str, Any]:
    """The network as the core writes it (docs/mesh.md §10.3); the core checks it for real."""
    outbound: dict[str, Any] = {"type": network.kind, "state-dir": f"mesh/{network.id}"}
    match network.settings:
        case TailscaleSettings() as settings:
            outbound |= {"auth-key": settings.auth_key, "control-url": settings.control_url}
        case ZeroTierSettings() as settings:
            outbound["network"] = settings.network.lower()
        case EasyTierSettings() as settings:
            outbound |= {"network-name": settings.network_name, "peers": list(settings.peers)}
    return {k: v for k, v in outbound.items() if v != ""}
