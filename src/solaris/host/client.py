"""One framed control connection; several requests may be in flight at once."""

from __future__ import annotations

import asyncio
import contextlib
import itertools

from google.protobuf.message import DecodeError

from solaris.host.base import HostError
from solaris.host.timeouts import request_timeout, set_host_deadline
from solaris.host.transport import Opener
from solaris.product import PROTOCOL_VERSION
from solaris.protocol import control_pb2
from solaris.protocol.frame import (
    KIND_REQUEST,
    KIND_RESPONSE,
    FrameError,
    encode,
    read_frame,
)


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
        self, request: control_pb2.Request, *, timeout: float | None = None
    ) -> control_pb2.Response:
        """Send request and return its response, raising HostError for an Error result."""
        if self._failure is not None:
            raise self._failure
        timeout = request_timeout(request, timeout)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        request_id = next(self._ids)
        request.protocol_version = PROTOCOL_VERSION
        request.id = request_id
        future = loop.create_future()
        self._pending[request_id] = future
        sent = False
        try:
            async with asyncio.timeout(timeout):
                async with self._write_lock:
                    if self._failure is not None:
                        raise self._failure
                    set_host_deadline(request, deadline - loop.time())
                    frame = encode(KIND_REQUEST, request.SerializeToString())
                    self._writer.write(frame)
                    await self._writer.drain()
                    sent = True
                response = await future
        except TimeoutError as error:
            if not sent:
                self._abort("sending a request timed out")
            message = f"request did not finish within {timeout:g}s"
            raise HostError("timeout", message, retryable=True) from error
        except asyncio.CancelledError:
            # Closing the control connection cancels work still queued at the host.
            # An admitted transaction remains bounded by the deadline in its request.
            self._abort("request cancelled")
            raise
        except OSError as error:
            raise HostError("connection_closed", str(error), retryable=True) from error
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # _abort may have failed this future while the call was waiting to send,
                # before it ever awaited the future itself.
                future.exception()
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
            try:
                async with asyncio.timeout(2):
                    await self._writer.wait_closed()
            except TimeoutError:
                pass

    def _abort(self, message: str) -> None:
        self._fail(HostError("connection_closed", message, retryable=True))
        self._reader_task.cancel()
        self._writer.close()

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
