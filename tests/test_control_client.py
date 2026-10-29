import asyncio
from collections.abc import Awaitable, Callable

import pytest

from mihomo_desktop.host.base import HostError, HostInfo
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
