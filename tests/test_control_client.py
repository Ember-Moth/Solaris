import asyncio
from collections.abc import Awaitable, Callable

import pytest

from mihomo_desktop.host.base import HostError, HostInfo, RunOptions, TunnelConfig
from mihomo_desktop.host.client import ControlClient
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Opener, Streams
from mihomo_desktop.protocol import control_pb2
from mihomo_desktop.protocol.frame import KIND_REQUEST, KIND_RESPONSE, encode, read_frame

type Answer = Callable[[control_pb2.Request], Awaitable[control_pb2.Response | None]]


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
                proxies=[pb.Proxy(name="香港 01", type="VLESS", delay_ms=68), pb.Proxy(name="X")],
            )
            return pb.Response(list_groups=pb.ListGroupsResponse(groups=[group]))
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

    options = RunOptions(tun_enabled=True, system_proxy_enabled=False, mixed_port=7890)
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
    expected_options = pb.RunOptions(tun_enabled=True, mixed_port=7890)
    assert start.start.config_yaml == "proxies: []\n"
    assert start.start.options == expected_options
    assert list(start.start.overrides) == list(overrides)
    assert list(reload.reload.overrides) == list(overrides)
    assert reload.reload.options == expected_options
