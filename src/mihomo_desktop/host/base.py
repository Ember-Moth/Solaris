"""Platform-neutral host boundary the models depend on (docs/architecture.md §7).

DTOs are immutable projections of the control protocol (proto/.../control.proto); enum
values are the protocol names without their prefix, lower-cased (``RUN_STATE_RUNNING``
becomes ``"running"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HostError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


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
    delay_ms: int | None  # None until a test succeeds


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
    host: str
    network: str
    rule: str
    chains: tuple[str, ...]  # outermost group first, final proxy last
    process: str
    upload_bytes: int
    download_bytes: int
    started_at_unix_ms: int


class TunnelHost(Protocol):
    async def get_info(self) -> HostInfo: ...

    async def get_status(self) -> HostStatus: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def set_mode(self, mode: str) -> None: ...

    async def set_tun(self, enabled: bool) -> None: ...

    async def set_system_proxy(self, enabled: bool) -> None: ...

    async def list_groups(self) -> list[ProxyGroup]: ...

    async def select_proxy(self, group: str, proxy: str) -> None: ...

    async def health_check(self, group: str) -> None: ...

    async def poll_traffic(self) -> list[TrafficSample]:
        """Samples recorded since the previous poll, oldest first."""
        ...

    async def poll_connections(self) -> list[Connection]:
        """The latest connection snapshot."""
        ...

    async def close(self) -> None: ...
