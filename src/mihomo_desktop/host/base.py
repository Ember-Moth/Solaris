"""Platform-neutral host boundary the models depend on (docs/ui.md).

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
class RunOptions:
    tun_enabled: bool = False
    system_proxy_enabled: bool = False
    mixed_port: int = 0  # 0 keeps the profile's own port
    allow_lan: bool = False


@dataclass(frozen=True)
class TunnelConfig:
    """What Validate, Start and Reload carry. Validate ignores ``options``."""

    config_yaml: str
    # Override YAML texts in the order they apply: the global one, then the profile's own.
    overrides: tuple[str, ...] = ()
    options: RunOptions = RunOptions()


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


LOG_LEVELS = ("debug", "info", "warning", "error")  # lowest first


@dataclass(frozen=True)
class LogEntry:
    level: str  # one of LOG_LEVELS
    message: str  # mihomo's text, untranslated
    time_unix_ms: int


class TunnelHost(Protocol):
    async def get_info(self) -> HostInfo: ...

    async def get_status(self) -> HostStatus: ...

    async def validate(self, config: TunnelConfig) -> None:
        """Run the config through the core's pipeline without applying it; raises
        HostError("invalid_config") when the core rejects it."""
        ...

    async def start(self, config: TunnelConfig) -> None: ...

    async def reload(self, config: TunnelConfig) -> None:
        """Replace the running config in place; raises HostError("not_running") when stopped."""
        ...

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
