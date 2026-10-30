"""An in-process TunnelHost, subscription fetcher and profiles with sample data.

Used by the tests and scripts/preview.py so the UI runs without the service, the system
extension or the network. Traffic follows a seeded random walk; connections open and close
and log lines arrive as the clock advances, all from seeded generators, so runs with a fake
clock are repeatable. What the core would answer about a configuration comes from the
stand-ins in simulated_core.py; what it reports at run time (WARP devices, VPN Gate's list,
mesh outbounds) is made-up sample data, which tests set to the state they need.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import yaml

from solaris.host.base import (
    EXPORT_SHARE,
    LOG_LEVELS,
    TAKEOVER_SETTINGS,
    TUN_STACKS,
    Composition,
    CompositionReport,
    Connection,
    GeoCategories,
    GroupPreview,
    HostError,
    HostInfo,
    HostStatus,
    LogEntry,
    MeshOutbound,
    MeshStatus,
    OpenVpnOptions,
    PlanConversion,
    Proxy,
    ProxyGroup,
    Rule,
    RuleMatch,
    RunOptions,
    SourceInspection,
    TrafficSample,
    TunnelConfig,
    VpnGateServer,
    WarpAccount,
    bundled_geo_categories,
    taken_over,
)
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
from tests.support.simulated_core import (
    VPNGATE_NAMES,
    check_config,
    compose,
    convert,
    export_yaml,
    info_filter_of,
    inspect,
    match,
    preview,
)

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


# The sample connections, open when the host starts and until something closes them:
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


# What login_mesh answers; a made-up Tailscale login page.
MESH_LOGIN = "https://login.tailscale.com/a/simulated"


def _sample_mesh_status(outbound: MeshOutbound, number: int) -> MeshStatus:
    """A running mesh outbound as the sample shows it: online, with made-up addresses."""
    status = MeshStatus(outbound.name, outbound.id, outbound.type, "online")
    match outbound.type:
        case "ZeroTier":
            return replace(
                status,
                addresses=(f"10.147.17.{number + 4}/24",),
                peer_count=2,
                node_id="a1b2c3d4e5",
                network_name="office",
            )
        case "EasyTier":
            return replace(status, addresses=(f"10.144.144.{number + 1}/24",), peer_count=2)
    return replace(
        status,
        addresses=(f"100.101.102.{number + 2}/32",),
        peer_count=4,
        network_name="example.ts.net",
    )


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
        # How the mesh outbounds stand, by name, in place of the sample online status; and
        # the outbounds login_mesh was asked for.
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
        info_filter_of(info_filter)
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
        return [
            self.mesh_states.get(outbound.name) or _sample_mesh_status(outbound, number)
            for number, outbound in enumerate(self.report.mesh if self.report else (), 1)
        ]

    async def login_mesh(self, name: str) -> str:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        if not any(m.name == name for m in (self.report.mesh if self.report else ())):
            raise HostError("invalid_request", f"no mesh outbound {name!r}")
        self.mesh_logins.append(name)
        return MESH_LOGIN

    async def stop(self) -> None:
        self._running = False
        self._tunnel_up = False
        self._failed = False
        self._connections.clear()

    async def set_mode(self, mode: str) -> None:
        if mode not in ("rule", "global", "direct"):
            raise HostError("invalid_request", f"unknown mode {mode}")
        self._mode = mode

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
        effective = self.effective if self.effective is not None else yaml.safe_load(SAMPLE_CONFIG)
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
    """Three sample profiles, with "示例订阅 A" in use: its own rules route, as the
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
