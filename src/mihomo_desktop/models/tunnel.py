"""Polls the host and keeps what the overview shows: status, traffic history and the
busiest connections."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from mihomo_desktop.host.base import Connection, HostStatus, ProxyGroup, TrafficSample, TunnelHost

HISTORY_LENGTH = 40
RECENT_LIMIT = 5


@dataclass(frozen=True)
class ConnectionRate:
    host: str
    route: str  # "视频服务 · 香港 01", or the single hop such as "DIRECT"
    download_bytes_per_second: int


@dataclass(frozen=True)
class TunnelSnapshot:
    status: HostStatus
    latest: TrafficSample | None
    down_history: tuple[int, ...]  # bytes per second, oldest first
    up_history: tuple[int, ...]
    recent: tuple[ConnectionRate, ...]
    groups: tuple[ProxyGroup, ...] | None  # None when this refresh skipped the groups


def _route(chains: tuple[str, ...]) -> str:
    if not chains:
        return ""
    return chains[0] if len(chains) == 1 else f"{chains[0]} · {chains[-1]}"


class TunnelSession:
    def __init__(self, host: TunnelHost, clock: Callable[[], float] = time.monotonic) -> None:
        self._host = host
        self._clock = clock
        self._down: deque[int] = deque([0] * HISTORY_LENGTH, maxlen=HISTORY_LENGTH)
        self._up: deque[int] = deque([0] * HISTORY_LENGTH, maxlen=HISTORY_LENGTH)
        self._latest: TrafficSample | None = None
        self._seen: dict[str, tuple[int, float]] = {}

    async def refresh(self, *, include_groups: bool = False) -> TunnelSnapshot:
        status = await self._host.get_status()
        recent: tuple[ConnectionRate, ...] = ()
        if status.state == "running":
            for sample in await self._host.poll_traffic():
                self._down.append(sample.down_bytes_per_second)
                self._up.append(sample.up_bytes_per_second)
                self._latest = sample
            recent = self._rates(await self._host.poll_connections())
        else:
            self._down.append(0)
            self._up.append(0)
            self._latest = None
            self._seen.clear()
        groups = tuple(await self._host.list_groups()) if include_groups else None
        return TunnelSnapshot(
            status=status,
            latest=self._latest,
            down_history=tuple(self._down),
            up_history=tuple(self._up),
            recent=recent,
            groups=groups,
        )

    def _rates(self, connections: list[Connection]) -> tuple[ConnectionRate, ...]:
        now = self._clock()
        rates: list[tuple[int, int, ConnectionRate]] = []
        seen: dict[str, tuple[int, float]] = {}
        for connection in connections:
            previous = self._seen.get(connection.id)
            rate = 0
            if previous is not None and now > previous[1]:
                delta = max(connection.download_bytes - previous[0], 0)
                rate = int(delta / (now - previous[1]))
            seen[connection.id] = (connection.download_bytes, now)
            item = ConnectionRate(connection.host, _route(connection.chains), rate)
            rates.append((rate, connection.started_at_unix_ms, item))
        self._seen = seen
        rates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return tuple(item for _, _, item in rates[:RECENT_LIMIT])
