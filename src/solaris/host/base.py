"""Platform-neutral host boundary the models depend on (docs/ui.md).

DTOs are immutable projections of the control protocol (proto/.../control.proto); enum
values are the protocol names without their prefix, lower-cased (``RUN_STATE_RUNNING``
becomes ``"running"``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from solaris.host import geo_categories


class HostError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


# mihomo's TUN stacks (docs/architecture.md §3.4); gvisor and mixed need a core built with
# with_gvisor.
TUN_STACKS = ("system", "mips", "gvisor", "mixed")


@dataclass(frozen=True)
class RunOptions:
    tun_enabled: bool = False
    system_proxy_enabled: bool = False
    mixed_port: int = 0  # 0 keeps the profile's own port
    allow_lan: bool = False
    tun_stack: str = ""  # one of TUN_STACKS; "" keeps the profile's own
    # macOS only (D19): the tunnel includes all networks. Not in the protocol: MacExtensionHost
    # writes it into the VPN configuration as it brings the tunnel up, and the extension tells
    # the core.
    include_all_networks: bool = False


# What connecting takes over on a host (D26, docs/ui.md §8).
TAKEOVER_SETTINGS = "settings"  # the Windows service: TUN and the system proxy as set
TAKEOVER_TUN = "tun"  # the macOS extension: always a TUN, never the system proxy
TAKEOVER_NONE = "none"  # the development service: neither, only the mixed port


def taken_over(options: RunOptions, takeover: str) -> RunOptions:
    """``options`` with the TUN and the system proxy a ``takeover`` host runs."""
    if takeover == TAKEOVER_TUN:
        return replace(options, tun_enabled=True, system_proxy_enabled=False)
    if takeover == TAKEOVER_NONE:
        return replace(options, tun_enabled=False, system_proxy_enabled=False)
    return options


@dataclass(frozen=True)
class CompositionSource:
    id: str
    name: str
    yaml: str  # the subscription as downloaded: a mihomo profile or share links


WARP_PROTOCOLS = ("masque-h2", "masque-h3", "wireguard")  # docs/exits.md §3.1


@dataclass(frozen=True)
class WarpAccount:
    """A Cloudflare WARP device, as register_warp made it (docs/exits.md §3.2). A credential:
    kept in the system's credential store, out of backups, and handed back unchanged in
    every composition."""

    device_id: str
    token: str
    protocol: str  # one of WARP_PROTOCOLS; the key is for it
    private_key: str
    peer_public_key: str
    endpoint: str  # host:port
    address_v4: str
    address_v6: str
    client_id: str  # base64
    registered_at_unix_ms: int


@dataclass(frozen=True)
class Egress:
    """What the chained exits need (docs/exits.md §7.1). An exit without its material is
    unavailable, and the rules that name it reject their connections (D21)."""

    warp: WarpAccount | None = None


@dataclass(frozen=True)
class Composition:
    """The node pool and a plan, for "my plan" routing: the core assembles the configuration
    (docs/routing.md §8). ``sources`` are the subscriptions in the pool in the order they were
    imported; ``plan_json`` follows routing.md §8.5."""

    sources: tuple[CompositionSource, ...]
    plan_json: str
    # Names of information nodes ("剩余流量"), in mihomo's regular expressions; "" keeps all.
    info_filter: str = ""
    egress: Egress = Egress()


@dataclass(frozen=True)
class TunnelConfig:
    """What Validate, Start and Reload carry: a subscription's own YAML, or a composition in
    its place. Validate ignores ``options``."""

    config_yaml: str = ""
    # Override YAML texts in the order they apply: the global one, then the profile's own.
    overrides: tuple[str, ...] = ()
    options: RunOptions = RunOptions()
    composition: Composition | None = None

    def __post_init__(self) -> None:
        if self.composition is not None and self.config_yaml:
            raise ValueError("a TunnelConfig carries config_yaml or a composition, not both")


@dataclass(frozen=True)
class CompositionWarning:
    entry_id: str  # a rule entry, or "fallback"
    # "region_empty": the target region has no nodes; "group_empty": the target group has no
    # members. Either way the entry goes to the built-in "proxy" group meanwhile.
    # "egress_unavailable", "mesh_missing": the target exit or mesh outbound is unavailable,
    # so the entry rejects its connections. "via_empty": an exit's via group (target) has no
    # members; entry_id is the exit, such as "egress:warp". "mesh_restricted": an option of
    # the mesh outbound entry_id that serves other devices (target) was turned off.
    reason: str
    target: str  # the target the plan names


@dataclass(frozen=True)
class ComposedGroup:
    id: str  # "proxy", "auto", "region:HK" or the plan's own id
    name: str  # in the generated configuration, as list_groups reports it
    kind: str  # "builtin", "region" or "custom"
    region: str  # region groups only: the region code
    node_count: int


@dataclass(frozen=True)
class Rule:
    type: str  # "GEOSITE", "MATCH", ...
    payload: str
    proxy: str  # the group or outbound the rule sends traffic to
    entry_id: str  # the plan's rule entry, "fallback", or "" when not from a plan


@dataclass(frozen=True)
class PoolNode:
    name: str  # in the generated configuration
    original_name: str  # in the subscription
    source_id: str
    type: str
    region: str  # region code; "ZZ" when none matched


@dataclass(frozen=True)
class MeshOutbound:
    """A Tailscale, ZeroTier or EasyTier outbound from a pool subscription: not a pool node,
    but a rule target of its own (docs/mesh.md §3)."""

    id: str  # "mesh:<subscription id>/<name in the subscription>", as plans name it
    name: str  # in the generated configuration
    type: str  # mihomo adapter type
    source_id: str


@dataclass(frozen=True)
class CompositionReport:
    """What the core made of a composition."""

    warnings: tuple[CompositionWarning, ...]
    groups: tuple[ComposedGroup, ...]  # in configuration order
    rules: tuple[Rule, ...]  # every generated rule, in order
    nodes: tuple[PoolNode, ...]  # the pool after exclusions
    mesh: tuple[MeshOutbound, ...] = ()


@dataclass(frozen=True)
class GroupPreview:
    """The members a group of a draft plan gets with the pool (routing.md §4.5)."""

    nodes: tuple[PoolNode, ...]  # in pool order
    # The other members by id: group ids, "region:JP" for each region group with nodes,
    # DIRECT, REJECT.
    others: tuple[str, ...]


@dataclass(frozen=True)
class RuleMatch:
    """The rule a connection would match (routing.md §8.3)."""

    matched: bool  # False: no rule matched, so mihomo goes DIRECT
    index: int  # the rule's position in the configuration
    rule: Rule | None
    # The rule's target first, then each group's current choice down to the node, as
    # Connection.chains; the target alone when the core does not run that group.
    chain: tuple[str, ...]
    resolved_ip: str  # what the domain resolved to for an IP rule; "" otherwise


@dataclass(frozen=True)
class SkippedRule:
    rule: str  # as the configuration wrote it
    # "rule_set", "sub_rule", "target" (neither a group nor an outbound), "after_match",
    # "invalid"
    reason: str


@dataclass(frozen=True)
class PlanConversion:
    """A subscription's groups and rules as a plan (routing.md §4.7)."""

    plan_json: str
    group_count: int
    merged_group_count: int  # merged into 节点选择 or 自动选择
    rule_count: int  # MATCH included
    raw_rule_count: int
    entry_count: int  # neighbouring rules may share an entry
    skipped: tuple[SkippedRule, ...]


EXPORT_SHARE = "share"  # proxy-groups and rules for another configuration
EXPORT_GENERATED = "generated"  # the whole configuration the core would run


@dataclass(frozen=True)
class SourceNode:
    name: str
    type: str  # mihomo adapter type, as in Proxy.type
    region: str  # region code; "ZZ" when none matched
    info: bool  # matches the info filter, so the pool leaves it out
    mesh: bool = False  # a Tailscale, ZeroTier or EasyTier outbound, kept out of the pool


@dataclass(frozen=True)
class SourceInspection:
    nodes: tuple[SourceNode, ...]
    has_rules: bool
    group_count: int
    rule_count: int


@dataclass(frozen=True)
class GeoDataset:
    names: tuple[str, ...]  # lower-cased and sorted, as rules write them
    sha256: str  # of the data file
    updated_at_unix_ms: int  # 0 when unknown
    bundled: bool  # the installer's list, because the core had no data to read


@dataclass(frozen=True)
class GeoCategories:
    geosite: GeoDataset
    geoip: GeoDataset


def bundled_geo_categories() -> GeoCategories:
    """The categories of the GEO data the installer ships, for when the core has none to list
    or cannot be reached (docs/routing.md §8.4)."""
    return GeoCategories(
        GeoDataset(geo_categories.GEOSITE, geo_categories.GEOSITE_SHA256, 0, True),
        GeoDataset(geo_categories.GEOIP, geo_categories.GEOIP_SHA256, 0, True),
    )


@dataclass(frozen=True)
class HostInfo:
    protocol_version: int
    service_version: str
    core_version: str
    state: str  # RunState without its prefix, e.g. "stopped"
    ownership: str  # Ownership without its prefix; "unspecified" on macOS


@dataclass(frozen=True)
class HostStatus:
    state: str
    mode: str  # "rule", "global" or "direct"
    started_at_unix_ms: int  # 0 while stopped
    memory_bytes: int
    connection_count: int
    tun_enabled: bool
    system_proxy_enabled: bool


@dataclass(frozen=True)
class Proxy:
    name: str
    type: str
    delay_ms: int | None  # None until tested; 0 when the last test failed


@dataclass(frozen=True)
class ProxyGroup:
    name: str
    type: str  # mihomo adapter type: "Selector", "URLTest", "Fallback", ...
    now: str
    proxies: tuple[Proxy, ...]

    @property
    def selectable(self) -> bool:
        return self.type == "Selector"


@dataclass(frozen=True)
class TrafficSample:
    up_bytes_per_second: int
    down_bytes_per_second: int
    up_total_bytes: int
    down_total_bytes: int


@dataclass(frozen=True)
class Connection:
    id: str
    host: str  # the requested domain without its port; empty for a connection to an IP
    network: str  # "tcp" or "udp", as mihomo writes it
    rule: str  # "DOMAIN-SUFFIX,github.com", or the type alone for "MATCH"
    chains: tuple[str, ...]  # outermost group first, final proxy last
    process: str  # the process name, or its path when the name is unknown; may be empty
    upload_bytes: int
    download_bytes: int
    started_at_unix_ms: int
    source: str = ""  # "198.18.0.1:52344"
    destination: str = ""  # "140.82.112.3:443"; brackets around an IPv6 address
    rule_entry_id: str = ""  # the plan's rule entry the rule came from, as Rule.entry_id


LOG_LEVELS = ("debug", "info", "warning", "error")  # lowest first


@dataclass(frozen=True)
class LogEntry:
    level: str  # one of LOG_LEVELS
    message: str  # mihomo's text, untranslated
    time_unix_ms: int


class TunnelHost(Protocol):
    # TAKEOVER_SETTINGS, TAKEOVER_TUN or TAKEOVER_NONE: the host runs every config with
    # taken_over(options, takeover), whatever the settings asked for.
    takeover: str

    async def get_info(self) -> HostInfo: ...

    async def get_status(self) -> HostStatus: ...

    async def validate(self, config: TunnelConfig) -> CompositionReport | None:
        """Run the config through the core's pipeline without applying it; raises
        HostError("invalid_config") when the core rejects it. A composition comes back with
        the core's report, a profile's own YAML with None; so for start and reload."""
        ...

    async def start(self, config: TunnelConfig) -> CompositionReport | None: ...

    async def reload(self, config: TunnelConfig) -> CompositionReport | None:
        """Replace the running config in place; raises HostError("not_running") when stopped."""
        ...

    async def inspect_source(self, yaml: str, info_filter: str = "") -> SourceInspection:
        """Read a subscription as the core does: its nodes with their regions, and whether it
        brings rules. Needs no running core; raises HostError("invalid_config") when the core
        cannot read it."""
        ...

    async def list_geo_categories(self) -> GeoCategories:
        """The GEOSITE and GEOIP categories of the core's GEO data; the installer's list for
        a kind the core has no data of."""
        ...

    async def preview_group(self, composition: Composition, group_id: str) -> GroupPreview:
        """The members of a group of the composition's plan; raises
        HostError("invalid_config") for a plan the core refuses."""
        ...

    async def match_rule(
        self, host: str, port: int = 0, network: str = "tcp", config: TunnelConfig | None = None
    ) -> RuleMatch:
        """The rule a connection to ``host`` (a domain or an IP) would match: in ``config``
        built on the side, or in the running configuration for None, which raises
        HostError("not_running") while the core is stopped. Port 0 means 443."""
        ...

    async def convert_to_plan(
        self, yaml: str, *, source_id: str = "", locale: str = "zh", info_filter: str = ""
    ) -> PlanConversion:
        """A subscription's or a configuration's groups and rules as a plan. ``source_id``
        is the pool's subscription it came from; "" matches its nodes by name."""
        ...

    async def export_plan(self, config: TunnelConfig, export: str = EXPORT_SHARE) -> str:
        """The composition's plan as YAML: EXPORT_SHARE or EXPORT_GENERATED, which applies
        ``config``'s overrides and options."""
        ...

    async def register_warp(
        self, via: str, protocol: str = "masque-h2", existing: WarpAccount | None = None
    ) -> WarpAccount:
        """Registers a WARP device through the running configuration's group ``via`` (its
        name), or gives ``existing`` a key for ``protocol`` (docs/exits.md §3.2). Raises
        HostError("not_running") while the core is stopped, and "operation_failed" with
        Cloudflare's text when registration fails."""
        ...

    async def stop(self) -> None: ...

    async def set_mode(self, mode: str) -> None: ...

    async def set_tun(self, enabled: bool) -> None: ...

    async def set_system_proxy(self, enabled: bool) -> None: ...

    async def list_groups(self) -> list[ProxyGroup]: ...

    async def list_rules(self) -> list[Rule]:
        """The running configuration's rules in order, with the plan's rule entries."""
        ...

    async def select_proxy(self, group: str, proxy: str) -> None: ...

    async def health_check(self, group: str) -> None: ...

    async def poll_traffic(self) -> list[TrafficSample]:
        """Samples recorded since the previous poll, oldest first."""
        ...

    async def poll_connections(self) -> list[Connection]:
        """The latest connection snapshot, oldest connection first. Polling again before the
        core takes a new one returns the same snapshot, so several screens can share it."""
        ...

    async def close_connection(self, connection_id: str) -> None:
        """Close one connection; one that has already ended is not an error. Raises
        HostError("not_running") when the core is stopped."""
        ...

    async def close_all_connections(self) -> None: ...

    async def poll_logs(self, level: str) -> list[LogEntry]:
        """mihomo's log lines at ``level`` (one of LOG_LEVELS) or above that arrived since the
        previous poll, oldest first. Lines arrive whether or not the core runs. The first
        poll starts collecting; a poll with another level starts again at that level, so
        lines below the previous level are not recovered."""
        ...

    async def close(self) -> None: ...
