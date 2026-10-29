"""An in-process TunnelHost, subscription fetcher and profiles with the prototype's
sample data.

Used by the tests and scripts/preview.py so the UI runs without the service, the system
extension or the network. Traffic follows a seeded random walk, so runs are repeatable.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yaml

from mihomo_desktop.host.base import (
    Connection,
    HostError,
    HostInfo,
    HostStatus,
    Proxy,
    ProxyGroup,
    TrafficSample,
    TunnelConfig,
)
from mihomo_desktop.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.models.subscription import Download

GB = 1024**3
MB = 1024**2

NODES: dict[str, tuple[str, int | None]] = {
    "香港 01": ("VLESS", 68),
    "香港 02": ("Hysteria2", 54),
    "香港 03": ("Trojan", 182),
    "日本 01": ("VLESS", 96),
    "日本 02": ("Shadowsocks", 241),
    "新加坡 01": ("Trojan", 127),
    "新加坡 02": ("VLESS", None),
    "美国 01": ("Hysteria2", 198),
    "美国 02": ("Shadowsocks", 412),
}


# The insert keys of an override (docs/architecture.md §7.4): key -> (target list, at front).
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
    """The simulated pipeline (architecture §3.2, §7.4): parse the config, apply the
    overrides in order, then clear the controller keys. The result must define a proxy,
    inline or through a provider. Failures use the service's error code."""
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
    proxies = document.get("proxies")
    providers = document.get("proxy-providers")
    if not (isinstance(proxies, list) and proxies) and not (
        isinstance(providers, dict) and providers
    ):
        raise HostError("invalid_config", "the config defines no proxies")
    return document


_CORE_TYPES = {"VLESS": "vless", "Hysteria2": "hysteria2", "Trojan": "trojan", "Shadowsocks": "ss"}
SAMPLE_CONFIG = (
    "mixed-port: 7890\nmode: rule\nproxies:\n"
    + "".join(
        f"  - {{name: {name}, type: {_CORE_TYPES[kind]}, server: node{i}.example.net, port: 443}}\n"
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
    host: str
    network: str
    rule: str
    target: str  # a group name or "DIRECT"
    process: str
    share: float  # fraction of the download rate this connection carries
    started_ago_s: int
    upload: int = 0
    download: int = 0


class SimulatedHost:
    def __init__(
        self,
        *,
        seed: int = 7,
        clock: Callable[[], float] = time.time,
        health_check_s: float = 0.0,
        development: bool = False,
    ) -> None:
        """``development`` refuses TUN and the system proxy as the development service
        does (docs/architecture.md §11)."""
        self._random = random.Random(seed)
        self._clock = clock
        self._health_check_s = health_check_s
        self._development = development
        self.busy_ports: set[int] = set()  # mixed ports that another program listens on
        self._running = True
        self.config: TunnelConfig | None = None  # the last config started or reloaded
        self.effective: dict[str, Any] | None = None  # that config after the pipeline
        self.reloads = 0
        self._mode = "rule"
        self._tun = not development
        self._system_proxy = False
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
        self._last_connections = clock()
        self._connections = [
            _Connection(
                "1",
                "www.youtube.com:443",
                "UDP",
                "GEOSITE,youtube",
                "视频服务",
                "Chrome",
                0.62,
                767,
            ),
            _Connection(
                "2",
                "registry.npmjs.org:443",
                "TCP",
                "DOMAIN-SUFFIX,npmjs.org",
                "节点选择",
                "node",
                0.12,
                38,
            ),
            _Connection(
                "3",
                "github.com:443",
                "TCP",
                "DOMAIN-SUFFIX,github.com",
                "节点选择",
                "Code",
                0.04,
                252,
            ),
            _Connection(
                "4", "www.wikipedia.org:443", "TCP", "MATCH", "节点选择", "Firefox", 0.02, 210
            ),
            _Connection(
                "5", "cdn.jsdelivr.net:443", "TCP", "MATCH", "节点选择", "Chrome", 0.01, 139
            ),
            _Connection(
                "6", "www.baidu.com:443", "TCP", "GEOSITE,cn", "DIRECT", "Safari", 0.002, 51
            ),
            _Connection(
                "7",
                "pypi.org:443",
                "TCP",
                "DOMAIN-SUFFIX,pypi.org",
                "节点选择",
                "python",
                0.0003,
                65,
            ),
            _Connection(
                "8", "223.5.5.5:53", "UDP", "IP-CIDR,223.5.5.5/32", "DIRECT", "内核", 0.00001, 9
            ),
        ]

    # -- TunnelHost --------------------------------------------------------------------------
    async def get_info(self) -> HostInfo:
        return HostInfo(1, "simulated", "mihomo v1.19.33", self._state(), "available")

    async def get_status(self) -> HostStatus:
        return HostStatus(
            state=self._state(),
            mode=self._mode,
            started_at_unix_ms=int(self._started_at * 1000) if self._running else 0,
            memory_bytes=48 * MB if self._running else 0,
            connection_count=len(self._connections) if self._running else 0,
            tun_enabled=self._tun,
            system_proxy_enabled=self._system_proxy,
        )

    async def validate(self, config: TunnelConfig) -> None:
        check_config(config)

    async def start(self, config: TunnelConfig) -> None:
        if self._running:
            raise HostError("already_running", "the core is already running")
        self._apply(config)
        self._running = True
        self._started_at = self._clock()
        self._last_poll = self._last_connections = self._clock()

    async def reload(self, config: TunnelConfig) -> None:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        self._apply(config)
        self.reloads += 1

    async def stop(self) -> None:
        self._running = False

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
            if name in NODES and self._delays[name] is not None:
                base = NODES[name][1] or 0
                self._delays[name] = max(20, base + self._random.randint(-25, 25))
        alive = [n for n in self._groups["自动选择"].members if self._delays[n] is not None]
        self._groups["自动选择"].now = min(alive, key=lambda n: self._delays[n] or 0)

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
        now = self._clock()
        elapsed = max(now - self._last_connections, 0.0)
        self._last_connections = now
        result = []
        for c in self._connections:
            c.download += int(self._down * c.share * elapsed)
            c.upload += int(self._up * c.share * elapsed)
            result.append(
                Connection(
                    id=c.id,
                    host=c.host,
                    network=c.network,
                    rule=c.rule,
                    chains=self._chains(c.target),
                    process=c.process,
                    upload_bytes=c.upload,
                    download_bytes=c.download,
                    started_at_unix_ms=int((now - c.started_ago_s) * 1000),
                )
            )
        return result

    async def close(self) -> None:
        pass

    # -- helpers -----------------------------------------------------------------------------
    def _apply(self, config: TunnelConfig) -> None:
        if self._development and config.options.system_proxy_enabled:
            raise HostError(
                "unsupported_method", "the development server never sets the system proxy"
            )
        if self._development and config.options.tun_enabled:
            raise HostError("unsupported_method", "this host never creates a TUN")
        self.effective = check_config(config)
        if config.options.mixed_port in self.busy_ports:
            message = f"listen tcp 127.0.0.1:{config.options.mixed_port}: address already in use"
            raise HostError("apply_failed", message, retryable=True)
        self.config = config
        self._tun = config.options.tun_enabled
        self._system_proxy = config.options.system_proxy_enabled

    def _state(self) -> str:
        return "running" if self._running else "stopped"

    def _proxy(self, name: str) -> Proxy:
        if name == "DIRECT":
            return Proxy("DIRECT", "Direct", None)
        if name in self._groups:
            final = self._final(name)
            return Proxy(name, self._groups[name].type, self._delays.get(final))
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
    """The prototype's three profiles, with "示例订阅 A" in use."""
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
    return seeded
