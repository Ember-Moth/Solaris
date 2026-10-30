"""The connections page's data: snapshots with per-connection rates, search, the TCP / UDP
filter and the sort.

The core reports byte totals only, so a rate is the change in a connection's totals over
the last few polls. Averaging over several polls also smooths over a poll that saw the same
snapshot twice (TunnelHost.poll_connections): the core takes one a second, as often as the
pages poll, so a poll now and then finds no new one. The overview ranks its busiest
connections with the same rates."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from solaris.host.base import Connection, TunnelHost

RATE_WINDOW = 4  # polls; about three seconds at one poll a second
NETWORKS = ("all", "tcp", "udp")
SORT_KEYS = ("rate", "traffic", "duration")
# Shown after the network for the ports whose protocol is plain, as in "TCP · HTTPS".
WELL_KNOWN = {
    ("tcp", "80"): "HTTP",
    ("tcp", "443"): "HTTPS",
    ("udp", "443"): "QUIC",
    ("tcp", "53"): "DNS",
    ("udp", "53"): "DNS",
}


@dataclass(frozen=True)
class TrackedConnection:
    connection: Connection
    upload_rate: int  # bytes per second
    download_rate: int

    @property
    def id(self) -> str:
        return self.connection.id

    @property
    def rate(self) -> int:
        return self.upload_rate + self.download_rate

    @property
    def total(self) -> int:
        return self.connection.upload_bytes + self.connection.download_bytes


@dataclass(frozen=True)
class ConnectionsState:
    running: bool
    connections: tuple[TrackedConnection, ...]  # in the core's order, oldest first


class RateTracker:
    """Each connection's rates over its last RATE_WINDOW polls."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # id -> (time, upload total, download total) for the last RATE_WINDOW polls
        self._samples: dict[str, deque[tuple[float, int, int]]] = {}

    def forget(self) -> None:
        """Drop the samples, so rates after a pause in polling start afresh."""
        self._samples.clear()

    def track(self, connections: Iterable[Connection]) -> tuple[TrackedConnection, ...]:
        now = self._clock()
        samples: dict[str, deque[tuple[float, int, int]]] = {}
        tracked = []
        for connection in connections:
            window = self._samples.get(connection.id) or deque(maxlen=RATE_WINDOW)
            window.append((now, connection.upload_bytes, connection.download_bytes))
            samples[connection.id] = window
            (start, up_start, down_start), (end, up_end, down_end) = window[0], window[-1]
            span = end - start
            up_rate = int(max(up_end - up_start, 0) / span) if span > 0 else 0
            down_rate = int(max(down_end - down_start, 0) / span) if span > 0 else 0
            tracked.append(TrackedConnection(connection, up_rate, down_rate))
        self._samples = samples
        return tuple(tracked)


class ConnectionMonitor:
    def __init__(self, host: TunnelHost, clock: Callable[[], float] = time.monotonic) -> None:
        self._host = host
        self._rates = RateTracker(clock)

    def forget(self) -> None:
        """Drop the samples, so rates after a pause in polling start afresh."""
        self._rates.forget()

    async def refresh(self) -> ConnectionsState:
        status = await self._host.get_status()
        if status.state != "running":
            self._rates.forget()
            return ConnectionsState(False, ())
        return ConnectionsState(True, self._rates.track(await self._host.poll_connections()))


def matches(tracked: TrackedConnection, query: str, network: str) -> bool:
    """``query`` is matched, ignoring case, against the host, destination, process, rule
    and chain."""
    connection = tracked.connection
    if network != "all" and connection.network.lower() != network:
        return False
    needle = query.strip().casefold()
    if not needle:
        return True
    fields = (
        connection.host,
        connection.destination,
        connection.process,
        connection.rule,
        *connection.chains,
    )
    return any(needle in field.casefold() for field in fields)


def arrange(
    connections: Iterable[TrackedConnection],
    query: str = "",
    network: str = "all",
    sort: str = "duration",
    descending: bool = True,
) -> list[TrackedConnection]:
    """The connections the table shows, in its order. Ties keep the oldest first so that
    rows with equal values do not trade places between polls."""
    shown = [c for c in connections if matches(c, query, network)]
    shown.sort(key=lambda c: (c.connection.started_at_unix_ms, c.id))
    if sort == "rate":
        shown.sort(key=lambda c: c.rate, reverse=descending)
    elif sort == "traffic":
        shown.sort(key=lambda c: c.total, reverse=descending)
    elif not descending:  # by duration, shortest first
        shown.reverse()
    return shown


def _port(connection: Connection) -> str:
    return connection.destination.rpartition(":")[2] if connection.destination else ""


def address(connection: Connection) -> str:
    """What the table shows as the host: the domain and port, or the destination."""
    if not connection.host:
        return connection.destination
    port = _port(connection)
    return f"{connection.host}:{port}" if port else connection.host


def protocol(connection: Connection) -> str:
    """``TCP``, or ``TCP · HTTPS`` when the port tells the protocol."""
    network = connection.network.lower()
    known = WELL_KNOWN.get((network, _port(connection)))
    return f"{network.upper()} · {known}" if known else network.upper()


CHAIN_SEPARATOR = " \u203a "  # a single right-pointing angle quotation mark


def chain(connection: Connection) -> str:
    """Every hop, outermost group first: ``节点选择 \u203a 香港 01``."""
    return CHAIN_SEPARATOR.join(connection.chains)


def process_name(connection: Connection) -> str:
    """The process's name, without the directories of a path."""
    return connection.process.replace("\\", "/").rpartition("/")[2]
