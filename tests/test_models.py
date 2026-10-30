import asyncio
from datetime import datetime

import pytest

from solaris.host.base import Connection, HostStatus, Proxy, ProxyGroup, TrafficSample
from solaris.models import format as fmt
from solaris.models.proxies import main_group, resolve, sort_proxies
from solaris.models.tunnel import HISTORY_LENGTH, TunnelSession
from tests.support.simulated import GB, MB, SimulatedHost


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (int(38.2 * GB), "38.2 GB"),
        (200 * GB, "200 GB"),
    ],
)
def test_size(value: int, text: str) -> None:
    assert fmt.size(value) == text


def test_rate_and_uptime() -> None:
    assert fmt.rate(int(8.6 * MB)) == "8.6 MB/s"
    assert fmt.uptime(8076) == "2:14:36"
    assert fmt.uptime(59) == "0:59"


@pytest.mark.parametrize(
    ("used", "total", "text"),
    [
        (int(38.2 * GB), 200 * GB, "38.2 / 200 GB"),
        (512 * MB, 200 * GB, "512 MB / 200 GB"),
        (0, 100 * GB, "0 B / 100 GB"),
    ],
)
def test_usage(used: int, total: int, text: str) -> None:
    assert fmt.usage(used, total) == text


def test_dates_and_ago() -> None:
    now = datetime(2026, 10, 29, 12).timestamp()
    assert fmt.date(datetime(2026, 12, 31).timestamp()) == "2026-12-31"
    assert fmt.ago(now - 30, now) == "刚刚"
    assert fmt.ago(now + 30, now) == "刚刚"  # a clock that went back
    assert fmt.ago(now - 5 * 60, now) == "5 分钟前"
    assert fmt.ago(now - 2 * 3600, now) == "2 小时前"
    assert fmt.ago(now - 3 * 86400, now) == "3 天前"
    assert fmt.ago(datetime(2026, 10, 20, 9).timestamp(), now) == "10 月 20 日"
    assert fmt.ago(datetime(2025, 10, 20, 9).timestamp(), now) == "2025-10-20"


@pytest.mark.parametrize(
    ("delay", "level"),
    [(None, "none"), (0, "timeout"), (68, "good"), (182, "fair"), (412, "poor")],
)
def test_latency_levels(delay: int | None, level: str) -> None:
    assert fmt.latency(delay)[1] == level


def test_nested_groups_resolve_to_the_final_proxy() -> None:
    groups = asyncio.run(SimulatedHost().list_groups())
    main = main_group(groups)
    assert main is not None
    assert main.name == "节点选择"
    resolved = resolve(groups, "视频服务")
    assert resolved is not None
    assert resolved.name == "香港 01"
    assert resolve(groups, "全球直连") == Proxy("DIRECT", "Direct", None)


def test_delay_sort_puts_untested_last() -> None:
    proxies = (Proxy("a", "VLESS", None), Proxy("b", "VLESS", 90), Proxy("c", "VLESS", 40))
    assert [p.name for p in sort_proxies(proxies, "delay")] == ["c", "b", "a"]
    assert [p.name for p in sort_proxies(proxies, "config")] == ["a", "b", "c"]


def test_session_tracks_history_and_connection_rates() -> None:
    now = [1000.0]
    host = SimulatedHost(clock=lambda: now[0])
    session = TunnelSession(host, clock=lambda: now[0])

    async def scenario() -> None:
        first = await session.refresh(include_groups=True)
        assert len(first.down_history) == HISTORY_LENGTH
        assert first.latest is not None
        assert first.groups is not None
        now[0] += 1
        second = await session.refresh()
        assert second.groups is None
        rates = [r.download_bytes_per_second for r in second.recent]
        assert rates == sorted(rates, reverse=True)
        assert second.recent[0].host == "www.youtube.com"
        assert second.recent[0].route == "视频服务 · 香港 01"

        await host.stop()
        stopped = await session.refresh()
        assert stopped.latest is None
        assert stopped.recent == ()
        assert stopped.down_history[-1] == 0

    asyncio.run(scenario())


def test_session_ranks_by_rates_over_several_polls() -> None:
    """The core takes a snapshot a second, as often as the overview polls, so a poll may find
    the last one again; the busiest connection keeps its rate meanwhile. A connection to an
    IP shows its destination."""
    now = [100.0]
    snapshots: list[list[Connection]] = []

    class Host:
        async def get_status(self) -> HostStatus:
            return HostStatus("running", "rule", 1, 0, 1, False, False)

        async def poll_traffic(self) -> list[TrafficSample]:
            return []

        async def poll_connections(self) -> list[Connection]:
            return snapshots.pop(0)

        async def list_groups(self) -> list[ProxyGroup]:
            return []

    session = TunnelSession(Host(), clock=lambda: now[0])  # pyright: ignore[reportArgumentType]

    async def poll(download: int) -> tuple[str, int]:
        stream = Connection(
            "a", "", "tcp", "MATCH", ("DIRECT",), "", 0, download, 1000, "", "10.0.0.2:8080"
        )
        snapshots.append([stream])
        (busiest,) = (await session.refresh()).recent
        now[0] += 1
        return busiest.host, busiest.download_bytes_per_second

    async def scenario() -> list[tuple[str, int]]:
        return [await poll(d) for d in (0, 1000, 1000, 3000)]

    hosts, rates = zip(*asyncio.run(scenario()), strict=True)
    assert set(hosts) == {"10.0.0.2:8080"}
    assert rates == (0, 1000, 500, 1000)
