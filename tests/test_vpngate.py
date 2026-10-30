"""The VPN Gate exit's list on this machine (docs/exits.md §5.2): which servers a plan takes,
and the list as the state table keeps it."""

from dataclasses import replace
from pathlib import Path

from solaris.host.base import VpnGateServer
from solaris.models import plans, vpngate
from solaris.models.database import read_state
from solaris.models.store import ProfileStore
from solaris.models.vpngate import ServerList, pick
from tests.support.simulated import VPNGATE_SERVERS

NOW = 1_800_000_000.0
JP = [s for s in VPNGATE_SERVERS if s.country == "JP"]


def hosts(servers: tuple[VpnGateServer, ...]) -> list[str]:
    return [s.host_name for s in servers]


def test_the_best_servers_of_the_countries() -> None:
    settings = plans.VpnGateExit(True, countries=("JP",), count=3)
    assert hosts(pick(VPNGATE_SERVERS, settings)) == hosts(tuple(JP[:3]))
    both = replace(settings, countries=("JP", "KR"), count=4)
    assert hosts(pick(VPNGATE_SERVERS, both)) == hosts(VPNGATE_SERVERS[:4])
    # Fewer servers than asked for: all there are.
    assert hosts(pick(VPNGATE_SERVERS, replace(settings, countries=("US",)))) == ["vpn572283910"]
    assert pick(VPNGATE_SERVERS, replace(settings, countries=("FR",))) == ()


def test_a_server_keeps_its_position() -> None:
    settings = plans.VpnGateExit(True, countries=("JP",), count=3)
    first = hosts(pick(VPNGATE_SERVERS, settings))
    # The second one failed: only its position changes hands, to the best one left.
    failing = frozenset({first[1]})
    again = hosts(pick(VPNGATE_SERVERS, settings, first, failing))
    assert again == [first[0], JP[3].host_name, first[2]]
    # Once it works again it stays out until a position is free.
    assert hosts(pick(VPNGATE_SERVERS, settings, again)) == again
    # A server gone from the list frees its position as well; fewer servers keep the first.
    listed = [s for s in VPNGATE_SERVERS if s.host_name != first[0]]
    assert hosts(pick(listed, settings, first))[1:] == first[1:]
    assert hosts(pick(VPNGATE_SERVERS, replace(settings, count=2), first)) == first[:2]


def test_the_list_goes_stale_and_failures_expire() -> None:
    servers = ServerList(VPNGATE_SERVERS, NOW, failed={"a": NOW - 10, "b": NOW - 2 * 3600})
    assert not servers.stale(NOW + 60)
    assert servers.stale(NOW + vpngate.REFRESH_S)
    assert ServerList().stale(NOW)
    assert servers.failing(NOW) == {"a"}
    assert servers.countries()[:2] == [("JP", 6), ("KR", 2)]


def test_the_list_lives_in_the_state_table(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path, clock=lambda: NOW)
    assert vpngate.load(store.database) == ServerList()
    picked = tuple(s.host_name for s in JP[:2])
    servers = ServerList(VPNGATE_SERVERS, NOW, picked, {"old": NOW - 7200, "new": NOW - 5})
    vpngate.save(store.database, servers)
    loaded = vpngate.load(store.database)
    # An hour-old failure is no longer kept.
    assert loaded == replace(servers, failed={"new": NOW - 5})
    assert loaded.picked_servers() == tuple(JP[:2])
    # A row it cannot read leaves the exit without servers rather than failing.
    with store.database.transaction() as connection:
        connection.execute("UPDATE state SET value = '{\"servers\": 1}' WHERE key = 'vpngate'")
    assert vpngate.load(store.database) == ServerList()
    with store.database.reading() as connection:
        assert read_state(connection, "vpngate") == {"servers": 1}
    store.close()
