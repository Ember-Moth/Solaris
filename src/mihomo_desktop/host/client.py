"""One framed control connection; several requests may be in flight at once."""

from __future__ import annotations

import asyncio
import contextlib
import itertools

from google.protobuf.message import DecodeError

from mihomo_desktop.host.base import HostError
from mihomo_desktop.host.transport import Opener
from mihomo_desktop.product import PROTOCOL_VERSION
from mihomo_desktop.protocol import control_pb2
from mihomo_desktop.protocol.frame import (
    KIND_REQUEST,
    KIND_RESPONSE,
    FrameError,
    encode,
    read_frame,
)

DEFAULT_TIMEOUT_S = 15.0


class ControlClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[control_pb2.Response]] = {}
        self._write_lock = asyncio.Lock()
        self._failure: HostError | None = None
        self._reader_task = asyncio.create_task(self._read_responses())

    @classmethod
    async def connect(cls, opener: Opener) -> ControlClient:
        reader, writer = await opener()
        return cls(reader, writer)

    @property
    def closed(self) -> bool:
        return self._failure is not None

    async def call(
        self, request: control_pb2.Request, *, timeout: float = DEFAULT_TIMEOUT_S
    ) -> control_pb2.Response:
        """Send request and return its response, raising HostError for an Error result."""
        if self._failure is not None:
            raise self._failure
        request_id = next(self._ids)
        request.protocol_version = PROTOCOL_VERSION
        request.id = request_id
        frame = encode(KIND_REQUEST, request.SerializeToString())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with self._write_lock:
                self._writer.write(frame)
                await self._writer.drain()
            response = await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            message = f"no response within {timeout:g}s"
            raise HostError("timeout", message, retryable=True) from error
        except OSError as error:
            raise HostError("connection_closed", str(error), retryable=True) from error
        finally:
            self._pending.pop(request_id, None)
        if response.WhichOneof("result") == "error":
            raise HostError(
                response.error.code, response.error.message, retryable=response.error.retryable
            )
        return response

    async def close(self) -> None:
        self._reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader_task
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()

    async def _read_responses(self) -> None:
        try:
            while True:
                response = control_pb2.Response.FromString(
                    await read_frame(self._reader, KIND_RESPONSE)
                )
                if response.protocol_version != PROTOCOL_VERSION:
                    raise FrameError(f"response protocol version {response.protocol_version}")
                future = self._pending.get(response.id)
                # A missing future is a late answer to a request that already timed out.
                if future is not None and not future.done():
                    future.set_result(response)
        except (asyncio.IncompleteReadError, FrameError, DecodeError, OSError) as error:
            self._fail(HostError("connection_closed", str(error), retryable=True))
        except asyncio.CancelledError:
            self._fail(HostError("connection_closed", "client closed", retryable=True))
            raise

    def _fail(self, failure: HostError) -> None:
        self._failure = failure
        for future in self._pending.values():
            if not future.done():
                future.set_exception(failure)
