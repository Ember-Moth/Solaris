"""An in-process TunnelHost and profile source with the prototype's sample data.

Used by the tests and scripts/preview.py so the UI runs without the service or the
system extension. Traffic follows a seeded random walk, so runs are repeatable.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

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
from mihomo_desktop.models.profiles import ProfileSummary

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


def check_config(config: TunnelConfig) -> None:
    """The simulated pipeline: the YAML must parse to a mapping that defines a proxy,
    either inline or through a provider. Failures use the service's error code."""
    try:
        document = yaml.safe_load(config.config_yaml)
    except yaml.YAMLError as error:
        raise HostError("invalid_config", str(error).replace("\n", " ")) from error
    if not isinstance(document, dict):
        raise HostError("invalid_config", "the config is not a YAML mapping")
    proxies = document.get("proxies")
    providers = document.get("proxy-providers")
    if not (isinstance(proxies, list) and proxies) and not (
        isinstance(providers, dict) and providers
    ):
        raise HostError("invalid_config", "the config defines no proxies")


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
    ) -> None:
        self._random = random.Random(seed)
        self._clock = clock
        self._health_check_s = health_check_s
        self._running = True
        self.config: TunnelConfig | None = None  # the last config started or reloaded
        self.reloads = 0
        self._mode = "rule"
        self._tun = True
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
        check_config(config)
        self._apply(config)
        self._running = True
        self._started_at = self._clock()
        self._last_poll = self._last_connections = self._clock()

    async def reload(self, config: TunnelConfig) -> None:
        if not self._running:
            raise HostError("not_running", "the core is not running")
        check_config(config)
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


class SampleProfiles:
    def active(self) -> ProfileSummary | None:
        return ProfileSummary(
            name="示例订阅 A",
            source="sub.example.com",
            updated="2 小时前",
            used_bytes=int(38.2 * GB),
            total_bytes=200 * GB,
            expires="2026-12-31",
        )
