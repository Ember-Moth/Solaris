"""The VPN Gate exit's servers on this machine (docs/exits.md §5.2): the list the core fetched
through the plan's via group, which of its servers the exit uses, and the ones that failed
lately. All of it lives in the database's ``state`` table and never goes into a backup.

Picking keeps the exit's group steady: the group hashes each site to a position, so a server
still usable keeps its position, and only the positions that lost theirs take the best of the
rest. A site keeps its address unless its own server went away.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from solaris.host.base import OpenVpnOptions, VpnGateServer
from solaris.models.database import Database, read_state, write_state
from solaris.models.plans import VpnGateExit

_log = logging.getLogger(__name__)

STATE_KEY = "vpngate"
REFRESH_S = 60 * 60  # the list is fetched again after an hour
FAILED_S = 60 * 60  # a server that failed is skipped for an hour
# Fetching again because servers failed waits at least this long after the last fetch, to
# spare VPN Gate's site; the refresh button does not wait.
MIN_REFRESH_S = 10 * 60


def outbound_name(server: VpnGateServer) -> str:
    """The server's outbound in the generated configuration, as the core names it."""
    return f"VPN Gate {server.country} {server.host_name}"


@dataclass(frozen=True)
class ServerList:
    servers: tuple[VpnGateServer, ...] = ()  # the list, best score first
    fetched_at: float = 0.0  # when the core fetched it; 0 for never
    picked: tuple[str, ...] = ()  # host names, in the order of the exit's group
    failed: Mapping[str, float] = field(default_factory=dict)  # host name -> when it failed
    # The last delay test of each picked server, by host name: None until tested, 0 when it
    # failed. What the core reported last; not stored.
    delays: Mapping[str, int | None] = field(default_factory=dict, compare=False)

    def stale(self, now: float) -> bool:
        return not self.servers or now - self.fetched_at >= REFRESH_S

    def picked_servers(self) -> tuple[VpnGateServer, ...]:
        by_host = {s.host_name: s for s in self.servers}
        return tuple(by_host[h] for h in self.picked if h in by_host)

    def countries(self) -> list[tuple[str, int]]:
        """The countries of the list with their server counts, most servers first."""
        counts: dict[str, int] = {}
        for server in self.servers:
            counts[server.country] = counts.get(server.country, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def failing(self, now: float) -> frozenset[str]:
        """The servers that failed within the last hour."""
        return frozenset(h for h, at in self.failed.items() if now - at < FAILED_S)


def pick(
    servers: Iterable[VpnGateServer],
    settings: VpnGateExit,
    previous: Iterable[str] = (),
    failing: frozenset[str] = frozenset(),
) -> tuple[VpnGateServer, ...]:
    """The exit's servers: the best ``settings.count`` in its countries that have not failed
    lately, each picked server that is still usable at its previous position."""
    usable = [s for s in servers if s.country in settings.countries and s.host_name not in failing]
    by_host = {s.host_name: s for s in usable}
    slots: list[VpnGateServer | None] = [by_host.get(h) for h in list(previous)[: settings.count]]
    slots += [None] * (settings.count - len(slots))
    taken = {s.host_name for s in slots if s is not None}
    rest = iter(s for s in usable if s.host_name not in taken)
    for n, slot in enumerate(slots):
        if slot is None:
            slots[n] = next(rest, None)
    return tuple(s for s in slots if s is not None)


# -- the state row ---------------------------------------------------------------------------
def load(database: Database) -> ServerList:
    """The stored list; an empty one when there is none, or it cannot be read."""
    with database.reading() as connection:
        value = read_state(connection, STATE_KEY)
    if value is None:
        return ServerList()
    try:
        return _from_json(value)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        _log.warning("ignoring the stored VPN Gate list: %s", error)
        return ServerList()


def save(database: Database, servers: ServerList) -> None:
    now = database.clock()
    value = {
        "servers": [asdict(s) for s in servers.servers],
        "fetched_at": servers.fetched_at,
        "picked": list(servers.picked),
        "failed": {h: at for h, at in servers.failed.items() if now - at < FAILED_S},
    }
    with database.transaction() as connection:
        write_state(connection, STATE_KEY, value, now)


def _from_json(value: Any) -> ServerList:
    servers = []
    for raw in value["servers"]:
        options = dict(raw["openvpn"])
        options["data_ciphers"] = tuple(options["data_ciphers"])
        servers.append(VpnGateServer(**{**raw, "openvpn": OpenVpnOptions(**options)}))
    failed = {str(h): float(at) for h, at in dict(value["failed"]).items()}
    picked = tuple(str(h) for h in value["picked"])
    return ServerList(tuple(servers), float(value["fetched_at"]), picked, failed)
