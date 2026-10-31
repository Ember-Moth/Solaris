import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from solaris.host.base import (
    Composition,
    EasyTierSettings,
    HostError,
    HostInfo,
    HostStatus,
    MeshNetwork,
    MeshStatus,
    RunOptions,
    TailscaleSettings,
    TunnelConfig,
    ZeroTierSettings,
)
from solaris.host.client import ControlClient
from solaris.host.service import ServiceHost
from solaris.host.transport import Opener, Streams
from solaris.protocol import control_pb2
from solaris.protocol.frame import KIND_REQUEST, KIND_RESPONSE, encode, read_frame

type Answer = Callable[[control_pb2.Request], Awaitable[control_pb2.Response | None]]


class BlockedWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        await asyncio.Event().wait()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


@pytest.mark.parametrize("waiting_for_lock", [False, True])
def test_timeout_includes_the_send_lock_and_drain(waiting_for_lock: bool) -> None:
    async def scenario() -> None:
        writer = BlockedWriter()
        client = ControlClient(asyncio.StreamReader(), cast(asyncio.StreamWriter, writer))
        if waiting_for_lock:
            await client._write_lock.acquire()
        try:
            with pytest.raises(HostError) as raised:
                async with asyncio.timeout(1):
                    await client.call(
                        control_pb2.Request(get_info=control_pb2.GetInfoRequest()), timeout=0.02
                    )
            assert raised.value.code == "timeout"
            assert writer.closed
            assert client.closed
            assert not client._pending
            assert bool(writer.frames) is not waiting_for_lock
        finally:
            if waiting_for_lock:
                client._write_lock.release()
            await client.close()

    asyncio.run(scenario())


def test_timeout_includes_opening_the_control_connection() -> None:
    async def unavailable() -> Streams:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def scenario() -> None:
        host = ServiceHost(unavailable)
        with pytest.raises(HostError) as raised:
            async with asyncio.timeout(1):
                await host._call(
                    control_pb2.Request(get_info=control_pb2.GetInfoRequest()), timeout=0.02
                )
        assert raised.value.code == "timeout"
        assert not host._connect_lock.locked()
        await host.close()

    asyncio.run(scenario())


def test_wire_budget_deducts_time_spent_waiting_to_send() -> None:
    received: list[control_pb2.Request] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        received.append(request)
        return info_response()

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            client = await ControlClient.connect(opener)
            await client._write_lock.acquire()
            started = time.time()
            task = asyncio.create_task(
                client.call(control_pb2.Request(get_info=control_pb2.GetInfoRequest()), timeout=0.5)
            )
            try:
                await asyncio.sleep(0.1)
                client._write_lock.release()
                await task
                sent = received[0]
                assert 0 < sent.timeout_ms <= 350
                assert started * 1000 < sent.deadline_unix_ms < (started + 0.5) * 1000
            finally:
                await client.close()

    asyncio.run(scenario())


async def serve(answer: Answer) -> tuple[asyncio.Server, Opener]:
    """A loopback stand-in for the service; None from answer drops the connection."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def respond(request: control_pb2.Request) -> None:
            response = await answer(request)
            if response is None:
                writer.close()
                return
            response.protocol_version = 1
            response.id = request.id
            writer.write(encode(KIND_RESPONSE, response.SerializeToString()))

        tasks: set[asyncio.Task[None]] = set()
        try:
            while True:
                payload = await read_frame(reader, KIND_REQUEST)
                task = asyncio.create_task(respond(control_pb2.Request.FromString(payload)))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except asyncio.IncompleteReadError, ConnectionError:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def opener() -> Streams:
        return await asyncio.open_connection("127.0.0.1", port)

    return server, opener


def info_response() -> control_pb2.Response:
    return control_pb2.Response(
        get_info=control_pb2.GetInfoResponse(
            protocol_version=1,
            service_version="dev",
            core_version="unlinked",
            state=control_pb2.RUN_STATE_STOPPED,
            ownership=control_pb2.OWNERSHIP_AVAILABLE,
        )
    )


def test_service_host_get_info() -> None:
    async def answer(_: control_pb2.Request) -> control_pb2.Response:
        return info_response()

    async def scenario() -> HostInfo:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                return await host.get_info()
            finally:
                await host.close()

    assert asyncio.run(scenario()) == HostInfo(1, "dev", "unlinked", "stopped", "available")


def test_error_result_raises_host_error() -> None:
    async def answer(_: control_pb2.Request) -> control_pb2.Response:
        return control_pb2.Response(
            error=control_pb2.Error(code="not_implemented", message="later", retryable=False)
        )

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            client = await ControlClient.connect(opener)
            try:
                await client.call(control_pb2.Request(get_status=control_pb2.GetStatusRequest()))
            finally:
                await client.close()

    with pytest.raises(HostError) as raised:
        asyncio.run(scenario())
    assert raised.value.code == "not_implemented"


def test_out_of_order_responses_match_their_requests() -> None:
    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        delay_ms = request.test_delay.timeout_ms
        await asyncio.sleep(delay_ms / 1000)
        return control_pb2.Response(test_delay=control_pb2.TestDelayResponse(delay_ms=delay_ms))

    async def scenario() -> list[int]:
        server, opener = await serve(answer)
        async with server:
            client = await ControlClient.connect(opener)
            try:
                calls = [
                    client.call(
                        control_pb2.Request(
                            test_delay=control_pb2.TestDelayRequest(timeout_ms=delay)
                        )
                    )
                    for delay in (60, 10, 30)
                ]
                return [response.test_delay.delay_ms for response in await asyncio.gather(*calls)]
            finally:
                await client.close()

    assert asyncio.run(scenario()) == [60, 10, 30]


def test_dropped_connection_fails_pending_calls() -> None:
    async def answer(_: control_pb2.Request) -> None:
        return None

    async def scenario() -> bool:
        server, opener = await serve(answer)
        async with server:
            client = await ControlClient.connect(opener)
            try:
                with pytest.raises(HostError) as raised:
                    await client.call(control_pb2.Request(get_info=control_pb2.GetInfoRequest()))
                assert raised.value.code == "connection_closed"
                return client.closed
            finally:
                await client.close()

    assert asyncio.run(scenario())


def test_service_host_maps_status_groups_and_traffic() -> None:
    pb = control_pb2
    subscribed: list[int] = []
    checks: list[control_pb2.HealthCheckRequest] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        method = request.WhichOneof("method")
        if method == "get_status":
            return pb.Response(
                get_status=pb.GetStatusResponse(
                    state=pb.RUN_STATE_RUNNING,
                    mode=pb.MODE_GLOBAL,
                    connection_count=3,
                    tun_enabled=True,
                )
            )
        if method == "list_groups":
            group = pb.ProxyGroup(
                name="节点选择",
                type="Selector",
                now="香港 01",
                proxies=[
                    pb.Proxy(name="香港 01", type="VLESS", delay_ms=68, tcp_delay_ms=0),
                    pb.Proxy(name="X"),
                ],
            )
            return pb.Response(list_groups=pb.ListGroupsResponse(groups=[group]))
        if method == "health_check":
            checks.append(request.health_check)
            return pb.Response(empty=pb.Empty())
        if method == "subscribe_traffic":
            subscribed.append(len(subscribed) + 1)
            return pb.Response(subscribe=pb.SubscribeResponse(subscription_id=subscribed[-1]))
        if method == "poll_events" and request.poll_events.subscription_id == 1:
            # The first subscription has expired; the host must subscribe again.
            return pb.Response(error=pb.Error(code="unknown_subscription", message="expired"))
        sample = pb.TrafficSample(up_bytes_per_second=1, down_bytes_per_second=2)
        return pb.Response(poll_events=pb.PollEventsResponse(events=[pb.Event(traffic=sample)]))

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                status = await host.get_status()
                assert (status.state, status.mode, status.tun_enabled) == (
                    "running",
                    "global",
                    True,
                )
                (group,) = await host.list_groups()
                assert group.selectable
                assert [p.delay_ms for p in group.proxies] == [68, None]
                assert [p.tcp_delay_ms for p in group.proxies] == [0, None]
                await host.health_check("节点选择")
                await host.health_check("节点选择", "tcp")
                assert [(c.group, c.test) for c in checks] == [
                    ("节点选择", pb.DELAY_TEST_UNSPECIFIED),
                    ("节点选择", pb.DELAY_TEST_TCP),
                ]
                (sample,) = await host.poll_traffic()
                assert sample.down_bytes_per_second == 2
                assert subscribed == [1, 2]
            finally:
                await host.close()

    asyncio.run(scenario())


def test_service_host_sends_the_config_with_validate_start_and_reload() -> None:
    pb = control_pb2
    received: list[control_pb2.Request] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        received.append(request)
        if request.WhichOneof("method") == "reload":
            return pb.Response(error=pb.Error(code="not_running", message="core is stopped"))
        return pb.Response(empty=pb.Empty())

    options = RunOptions(
        tun_enabled=True, system_proxy_enabled=False, mixed_port=7890, tun_stack="mixed"
    )
    overrides = ("mode: global\n", "prepend-rules: [MATCH,DIRECT]\n")
    config = TunnelConfig("proxies: []\n", overrides, options)

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                await host.validate(config)
                await host.start(config)
                with pytest.raises(HostError) as raised:
                    await host.reload(config)
                assert raised.value.code == "not_running"
            finally:
                await host.close()

    asyncio.run(scenario())
    validate, start, reload = received
    assert validate.validate == pb.ValidateRequest(
        config_yaml="proxies: []\n", overrides=list(overrides)
    )
    expected_options = pb.RunOptions(
        tun_enabled=True, mixed_port=7890, tun_stack=pb.TUN_STACK_MIXED
    )
    assert start.start.config_yaml == "proxies: []\n"
    assert start.start.options == expected_options
    assert list(start.start.overrides) == list(overrides)
    assert list(reload.reload.overrides) == list(overrides)
    assert reload.reload.options == expected_options


def test_service_host_shares_the_latest_connection_snapshot() -> None:
    pb = control_pb2
    snapshots = [
        [pb.Event(connections=pb.ConnectionSnapshot())],
        [
            pb.Event(
                connections=pb.ConnectionSnapshot(
                    connections=[
                        pb.Connection(
                            id="a",
                            network="tcp",
                            source="198.18.0.1:52344",
                            destination="140.82.112.3:443",
                            host="github.com",
                            chains=["香港 01", "节点选择"],
                            rule="DomainSuffix",
                            rule_payload="github.com",
                            rule_entry_id="dev",
                            download_bytes=2048,
                        )
                    ]
                )
            )
        ],
        [],  # no new snapshot since the last poll
    ]

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        if request.WhichOneof("method") == "subscribe_connections":
            return pb.Response(subscribe=pb.SubscribeResponse(subscription_id=1))
        events = snapshots.pop(0)
        return pb.Response(poll_events=pb.PollEventsResponse(events=events))

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                assert await host.poll_connections() == []
                (connection,) = await host.poll_connections()
                assert connection.chains == ("节点选择", "香港 01")  # outermost group first
                assert connection.rule == "DomainSuffix,github.com"
                assert connection.destination == "140.82.112.3:443"
                assert connection.rule_entry_id == "dev"
                assert await host.poll_connections() == [connection]
            finally:
                await host.close()

    asyncio.run(scenario())


def test_service_host_closes_connections() -> None:
    pb = control_pb2
    received: list[str] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        received.append(request.WhichOneof("method") or "")
        if request.close_connection.id == "gone":
            return pb.Response(error=pb.Error(code="invalid_request", message='no connection "x"'))
        if request.WhichOneof("method") == "close_all_connections":
            return pb.Response(error=pb.Error(code="not_running", message="core is stopped"))
        return pb.Response(empty=pb.Empty())

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                await host.close_connection("a")
                await host.close_connection("gone")  # it ended by itself meanwhile
                with pytest.raises(HostError) as raised:
                    await host.close_all_connections()
                assert raised.value.code == "not_running"
            finally:
                await host.close()

    asyncio.run(scenario())
    assert received == ["close_connection", "close_connection", "close_all_connections"]


def test_service_host_subscribes_to_logs_again_for_another_level() -> None:
    pb = control_pb2
    subscribed: list[int] = []
    unsubscribed: list[int] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        method = request.WhichOneof("method")
        if method == "subscribe_logs":
            subscribed.append(request.subscribe_logs.level)
            return pb.Response(subscribe=pb.SubscribeResponse(subscription_id=len(subscribed)))
        if method == "unsubscribe":
            unsubscribed.append(request.unsubscribe.subscription_id)
            return pb.Response(empty=pb.Empty())
        entry = pb.LogEntry(level=pb.LOG_LEVEL_WARNING, message="[TCP] dial", time_unix_ms=5)
        return pb.Response(poll_events=pb.PollEventsResponse(events=[pb.Event(log=entry)]))

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                (entry,) = await host.poll_logs("info")
                assert (entry.level, entry.message, entry.time_unix_ms) == (
                    "warning",
                    "[TCP] dial",
                    5,
                )
                await host.poll_logs("info")
                await host.poll_logs("debug")
            finally:
                await host.close()

    asyncio.run(scenario())
    assert subscribed == [pb.LOG_LEVEL_INFO, pb.LOG_LEVEL_DEBUG]
    assert unsubscribed == [1]


def _not_ours(code: str) -> control_pb2.Response:
    return control_pb2.Response(error=control_pb2.Error(code=code, message=code))


def test_service_host_claims_before_acting_but_not_to_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A free service answers not_claimed (docs/architecture.md §5.4). Reads answer as for a
    stopped core; anything else claims it, waits out the restart a new owner needs, and
    goes again."""
    pb = control_pb2
    monkeypatch.setattr("solaris.host.service.CLAIM_RETRY_S", 0.01)
    claims = 0
    seen: list[str] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        nonlocal claims
        method = request.WhichOneof("method") or ""
        seen.append(method)
        if method == "claim":
            claims += 1
            if claims == 1:
                return _not_ours("service_restarting")
            return pb.Response(empty=pb.Empty())
        if claims < 2:
            return _not_ours("not_claimed")
        return pb.Response(empty=pb.Empty())

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                stopped = HostStatus("stopped", "rule", 0, 0, 0, False, False)
                assert await host.get_status() == stopped
                assert await host.list_groups() == []
                assert await host.list_rules() == []
                assert await host.poll_traffic() == []
                assert await host.poll_connections() == []
                assert await host.poll_logs("info") == []
                assert (await host.list_geo_categories()).geosite.bundled
                await host.stop()
                await host.close_all_connections()
                assert "claim" not in seen
                assert await host.validate(TunnelConfig("mixed-port: 7890\n")) is None
                assert seen[-4:] == ["validate", "claim", "claim", "validate"]
            finally:
                await host.close()

    asyncio.run(scenario())


def test_service_host_leaves_another_users_tunnel_alone() -> None:
    seen: list[str] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        seen.append(request.WhichOneof("method") or "")
        return _not_ours("owned_by_other_user")

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                for action in (host.get_status(), host.validate(TunnelConfig("a: 1\n"))):
                    with pytest.raises(HostError) as raised:
                        await action
                    assert raised.value.code == "owned_by_other_user"
            finally:
                await host.close()

    asyncio.run(scenario())
    assert "claim" not in seen


def test_service_host_takes_over_through_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """The UI never asks for a take-over itself: the elevated helper does (D5b), and the UI
    claims the service once it restarted for this user."""
    monkeypatch.setattr("solaris.host.service.CLAIM_RETRY_S", 0.01)
    pb = control_pb2
    taken_over = False
    claims: list[bool] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        if request.WhichOneof("method") == "claim":
            claims.append(request.claim.take_over)
        if not taken_over:
            return _not_ours("owned_by_other_user")
        return pb.Response(empty=pb.Empty())

    def helper() -> None:
        nonlocal taken_over
        taken_over = True

    def declined() -> None:
        raise HostError("cancelled", "the elevation was declined")

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                with pytest.raises(HostError) as raised:
                    await host.validate(TunnelConfig("a: 1\n"))
                assert raised.value.code == "owned_by_other_user"
                with pytest.raises(HostError) as raised:
                    await host.take_over(declined)
                assert raised.value.code == "cancelled"
                assert claims == []
                await host.take_over(helper)
                assert claims == [False]
                assert await host.validate(TunnelConfig("a: 1\n")) is None
            finally:
                await host.close()

    asyncio.run(scenario())


def test_service_host_gives_up_on_a_restart_that_does_not_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("solaris.host.service.CLAIM_RETRY_S", 0.01)
    monkeypatch.setattr("solaris.host.service.CLAIM_TIMEOUT_S", 0.1)

    async def answer(_: control_pb2.Request) -> control_pb2.Response:
        return _not_ours("service_restarting")

    async def scenario() -> None:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                await host.claim()
            finally:
                await host.close()

    with pytest.raises(HostError) as raised:
        asyncio.run(scenario())
    assert raised.value.code == "timeout"


def test_service_host_sends_mesh_networks_and_reads_their_status() -> None:
    pb = control_pb2
    received: list[control_pb2.Request] = []

    async def answer(request: control_pb2.Request) -> control_pb2.Response:
        received.append(request)
        match request.WhichOneof("method"):
            case "get_mesh_status":
                status = pb.MeshStatus(
                    name="家里",
                    id="mesh:net-home",
                    type="Tailscale",
                    state=pb.MESH_STATE_NEEDS_LOGIN,
                    login_url="https://login.tailscale.com/a/1",
                )
                unknown = pb.MeshStatus(name="旧的", type="EasyTier")  # no state yet
                return pb.Response(
                    get_mesh_status=pb.GetMeshStatusResponse(networks=[status, unknown])
                )
            case "login_mesh":
                return pb.Response(
                    login_mesh=pb.LoginMeshResponse(login_url="https://login.tailscale.com/a/2")
                )
        return pb.Response(empty=pb.Empty())

    networks = (
        MeshNetwork(
            "net-home", "家里", TailscaleSettings("tskey", "https://hs.example", "laptop"), "proxy"
        ),
        MeshNetwork("net-office", "公司", ZeroTierSettings("8056c2e21c000001", "identity")),
        MeshNetwork(
            "net-lab",
            "实验室",
            EasyTierSettings("lab", "s", ("tcp://a:1",), "10.144.144.2/24", "pc"),
        ),
    )
    config = TunnelConfig(composition=Composition((), '{"version": 1}', mesh=networks))

    async def scenario() -> tuple[list[MeshStatus], str]:
        server, opener = await serve(answer)
        async with server:
            host = ServiceHost(opener)
            try:
                await host.validate(config)
                return await host.get_mesh_status(), await host.login_mesh("家里")
            finally:
                await host.close()

    statuses, url = asyncio.run(scenario())
    validate, _, login = received
    sent = validate.validate.composition.mesh
    assert sent[0] == pb.MeshNetwork(
        id="net-home",
        name="家里",
        via="proxy",
        tailscale=pb.TailscaleNetwork(
            auth_key="tskey", control_url="https://hs.example", hostname="laptop"
        ),
    )
    assert sent[1].zerotier == pb.ZeroTierNetwork(
        network_id="8056c2e21c000001", identity_secret="identity"
    )
    assert sent[2].easytier == pb.EasyTierNetwork(
        network_name="lab",
        network_secret="s",
        peers=["tcp://a:1"],
        ipv4="10.144.144.2/24",
        hostname="pc",
    )
    assert statuses == [
        MeshStatus(
            "家里",
            "mesh:net-home",
            "Tailscale",
            "needs_login",
            login_url="https://login.tailscale.com/a/1",
        ),
        MeshStatus("旧的", "", "EasyTier", "starting"),
    ]
    assert login.login_mesh.name == "家里"
    assert url == "https://login.tailscale.com/a/2"
