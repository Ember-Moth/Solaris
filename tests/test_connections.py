import asyncio
import random
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any

import pytest
from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from PySide6.QtTest import QAbstractItemModelTester
from pytestqt.qtbot import QtBot

from solaris.host.base import Connection, HostStatus
from solaris.models.connections import (
    ConnectionMonitor,
    TrackedConnection,
    address,
    arrange,
    chain,
    process_name,
    protocol,
)
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.connections import ConnectionsViewModel
from tests.support.simulated import SimulatedHost

NOW = 1_800_000_000.0  # a clock that stands still, so no connection opens or closes


def prop(view_model: Any, name: str) -> Any:
    return view_model.property(name)


def still_host() -> SimulatedHost:
    return SimulatedHost(clock=lambda: NOW)


def connection(id: str, **fields: Any) -> Connection:
    defaults: dict[str, Any] = {
        "host": f"{id}.example.com",
        "network": "tcp",
        "rule": "MATCH",
        "chains": ("节点选择", "香港 01"),
        "process": "curl",
        "upload_bytes": 0,
        "download_bytes": 0,
        "started_at_unix_ms": 1000,
        "destination": "198.18.0.1:443",
    }
    return Connection(id=id, **{**defaults, **fields})


def tracked(id: str, rate: int = 0, **fields: Any) -> TrackedConnection:
    return TrackedConnection(connection(id, **fields), 0, rate)


# -- models -----------------------------------------------------------------------------------
def test_rates_average_over_the_last_polls() -> None:
    now = [100.0]
    snapshots: list[list[Connection]] = []

    class Host:
        async def get_status(self) -> HostStatus:
            return HostStatus("running", "rule", 0, 0, 1, False, False)

        async def poll_connections(self) -> list[Connection]:
            return snapshots.pop(0)

    monitor = ConnectionMonitor(Host(), clock=lambda: now[0])  # pyright: ignore[reportArgumentType]

    async def poll(download: int) -> int:
        snapshots.append([connection("a", download_bytes=download, upload_bytes=download // 10)])
        (only,) = (await monitor.refresh()).connections
        now[0] += 1
        return only.download_rate

    async def scenario() -> list[int]:
        # The third poll saw the same snapshot as the second; the window smooths it out.
        return [await poll(d) for d in (0, 1000, 1000, 4000, 6000, 8000)]

    assert asyncio.run(scenario()) == [0, 1000, 500, 1333, 1666, 2333]


def test_a_stopped_core_has_no_connections() -> None:
    host = SimulatedHost()
    monitor = ConnectionMonitor(host)

    async def scenario() -> None:
        assert (await monitor.refresh()).running
        await host.stop()
        state = await monitor.refresh()
        assert not state.running
        assert state.connections == ()

    asyncio.run(scenario())


def test_search_and_network_filter() -> None:
    rows = [
        tracked("a", host="github.com", process="Code Helper"),
        tracked(
            "b", host="", destination="223.5.5.5:53", network="udp", rule="IP-CIDR,223.5.5.5/32"
        ),
        tracked("c", host="www.baidu.com", chains=("DIRECT",)),
    ]
    assert [c.id for c in arrange(rows, "GITHUB")] == ["a"]
    assert [c.id for c in arrange(rows, "code")] == ["a"]  # process
    assert [c.id for c in arrange(rows, "223.5")] == ["b"]  # destination and rule
    assert [c.id for c in arrange(rows, "direct")] == ["c"]  # chain
    assert [c.id for c in arrange(rows, network="udp")] == ["b"]
    assert [c.id for c in arrange(rows, "  ", network="tcp")] == ["a", "c"]


def test_sorts_largest_first_and_keeps_ties_in_place() -> None:
    rows = [
        tracked("old", rate=5, started_at_unix_ms=1000, download_bytes=10),
        tracked("new", rate=50, started_at_unix_ms=3000, download_bytes=10),
        tracked("mid", rate=5, started_at_unix_ms=2000, download_bytes=900),
    ]
    assert [c.id for c in arrange(rows)] == ["old", "mid", "new"]  # longest first
    assert [c.id for c in arrange(rows, descending=False)] == ["new", "mid", "old"]
    assert [c.id for c in arrange(rows, sort="rate")] == ["new", "old", "mid"]
    assert [c.id for c in arrange(rows, sort="rate", descending=False)] == ["old", "mid", "new"]
    assert [c.id for c in arrange(rows, sort="traffic")] == ["mid", "old", "new"]


def test_table_text() -> None:
    github = connection("a", host="github.com", destination="140.82.112.3:443")
    assert address(github) == "github.com:443"
    assert protocol(github) == "TCP · HTTPS"
    assert chain(github) == "节点选择 \u203a 香港 01"
    dns = connection("b", host="", network="udp", destination="223.5.5.5:53")
    assert address(dns) == "223.5.5.5:53"
    assert protocol(dns) == "UDP · DNS"
    assert protocol(connection("c", network="udp", destination="17.253.84.253:123")) == "UDP"
    path = connection("d", process="C:\\Program Files\\Google\\Chrome\\chrome.exe")
    assert process_name(path) == "chrome.exe"
    assert process_name(connection("e", process="/usr/bin/curl")) == "curl"


# -- KeyedListModel ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Row:
    id: str
    value: int


def test_keyed_list_follows_random_updates(qtbot: QtBot) -> None:
    model = KeyedListModel(["id", "value"])
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    pool = [Row(str(n), 0) for n in range(40)]
    rng = random.Random(3)
    for _ in range(200):
        rows = rng.sample(pool, rng.randint(0, len(pool)))
        rows = [replace(r, value=rng.randint(0, 3)) for r in rows]
        model.sync(rows)
        assert list(model.rows) == rows
        assert model.rowCount() == len(rows)


def test_keyed_list_keeps_rows_and_persistent_indexes(qtbot: QtBot) -> None:
    model = KeyedListModel(["id", "value"])
    model.sync([Row("a", 1), Row("b", 2), Row("c", 3)])
    tracked_b = QPersistentModelIndex(model.index(1))
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    model.rowsInserted.connect(lambda _, first, last: inserted.append((first, last)))
    model.rowsRemoved.connect(lambda _, first, last: removed.append((first, last)))
    resets: list[bool] = []
    model.modelReset.connect(lambda: resets.append(True))

    model.sync([Row("d", 0), Row("c", 3), Row("b", 5)])

    assert removed == [(0, 0)]
    assert inserted == [(0, 0)]
    assert resets == []
    assert tracked_b.row() == 2
    roles = {bytes(name.data()).decode(): role for role, name in model.roleNames().items()}
    assert model.data(model.index(2), roles["id"]) == "b"
    assert model.data(model.index(2), roles["value"]) == 5
    assert model.data(QModelIndex(), roles["value"]) is None


def test_keyed_list_syncs_thousands_of_rows_quickly(qtbot: QtBot) -> None:
    import time

    model = KeyedListModel(["id", "value"])
    rng = random.Random(5)
    rows = [Row(str(n), 0) for n in range(3000)]
    started = time.perf_counter()
    for tick in range(10):
        rng.shuffle(rows)  # a sort by rate reorders everything
        rows = [replace(r, value=tick) for r in rows[50:]] + [
            Row(f"{tick}-{n}", 0) for n in range(50)
        ]
        model.sync(rows)
    assert time.perf_counter() - started < 2.0
    assert list(model.rows) == rows


# -- ConnectionsViewModel ---------------------------------------------------------------------
@pytest.fixture
def runner(qtbot: QtBot) -> Iterator[AsyncRunner]:
    built = AsyncRunner()
    built.start()
    yield built
    built.shutdown()


def test_connections_page_lists_filters_and_sorts(qtbot: QtBot, runner: AsyncRunner) -> None:
    connections = ConnectionsViewModel(still_host(), runner, lambda: NOW)
    connections.setActive(True)
    qtbot.waitUntil(lambda: prop(connections, "total") == 8)
    assert prop(connections, "summary") == "8 个"
    model = connections.model
    hosts = [row.host for row in model.rows]  # pyright: ignore[reportAttributeAccessIssue]
    assert hosts[0] == "www.youtube.com:443"  # open longest

    connections.setQuery("GitHub")
    assert [row.host for row in model.rows] == ["github.com:443"]  # pyright: ignore[reportAttributeAccessIssue]
    assert prop(connections, "summary") == "1 / 8 个"
    connections.setQuery("")
    connections.setNetwork("udp")
    assert {row.protocol for row in model.rows} == {"UDP · QUIC", "UDP · DNS"}  # pyright: ignore[reportAttributeAccessIssue]
    connections.setNetwork("all")
    connections.setQuery("nothing like this")
    assert prop(connections, "emptyText") == "没有匹配的连接"
    connections.setQuery("")

    connections.sortBy("duration")  # the current key: the order flips
    assert not prop(connections, "sortDescending")
    assert model.rows[-1].host == "www.youtube.com:443"  # pyright: ignore[reportAttributeAccessIssue]
    connections.sortBy("traffic")
    assert prop(connections, "sortKey") == "traffic"
    assert prop(connections, "sortDescending")
    connections.stop()


def test_closing_connections_and_the_detail(qtbot: QtBot, runner: AsyncRunner) -> None:
    host = still_host()
    connections = ConnectionsViewModel(host, runner, lambda: NOW)
    connections.setActive(True)
    qtbot.waitUntil(lambda: prop(connections, "total") == 8)
    target = connections.model.rows[1].id  # pyright: ignore[reportAttributeAccessIssue]

    connections.select(target)
    assert prop(connections, "selectedIndex") == 1
    assert prop(connections, "detail")["host"] == "github.com:443"
    assert prop(connections, "detail")["chain"] == "节点选择 \u203a 香港 01"
    assert not prop(connections, "detailClosed")
    connections.selectIndex(99)  # clamped to the last row
    assert prop(connections, "selectedIndex") == 7
    connections.select(target)

    connections.closeConnection(target)
    qtbot.waitUntil(lambda: prop(connections, "total") == 7)
    assert prop(connections, "detailClosed")  # the detail keeps its last values
    assert prop(connections, "detail")["host"] == "github.com:443"
    assert prop(connections, "selectedIndex") == -1

    connections.closeAll()
    qtbot.waitUntil(lambda: prop(connections, "total") == 0)
    assert prop(connections, "emptyText") == "没有连接"

    connections.setActive(False)
    asyncio.run(host.stop())
    connections.refresh()
    qtbot.waitUntil(lambda: not prop(connections, "running"))
    assert prop(connections, "summary") == "未连接"
    assert prop(connections, "emptyText") == "未连接"
    connections.closeAll()
    qtbot.waitUntil(lambda: prop(connections, "hasError"))
    assert prop(connections, "summary") == "隧道未运行"
    connections.stop()


def test_connections_summary_in_english(qtbot: QtBot, runner: AsyncRunner, english: None) -> None:
    connections = ConnectionsViewModel(still_host(), runner, lambda: NOW)
    connections.setActive(True)
    qtbot.waitUntil(lambda: prop(connections, "total") == 8)
    assert prop(connections, "summary") == "8 connections"
    connections.setQuery("github")
    assert prop(connections, "summary") == "1 of 8 connections"
    assert [o["label"] for o in prop(connections, "networkOptions")] == ["All", "TCP", "UDP"]
    connections.stop()


def test_connections_poll_only_while_shown(qtbot: QtBot, runner: AsyncRunner) -> None:
    host = still_host()
    polls: list[int] = []
    poll = host.poll_connections

    async def counted() -> list[Connection]:
        polls.append(1)
        return await poll()

    host.poll_connections = counted  # pyright: ignore[reportAttributeAccessIssue]
    connections = ConnectionsViewModel(host, runner)
    connections.setActive(True)
    qtbot.waitUntil(lambda: len(polls) == 1)
    connections.setActive(False)
    qtbot.wait(1300)
    assert len(polls) == 1
    connections.stop()
