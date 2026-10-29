"""Stream openers for the framed control protocol.

Windows talks to the service over its named pipe. The Unix socket only serves the
development build of the service (docs/architecture.md §11); the macOS release talks
to its system extension through provider messages instead.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from mihomo_desktop.host.base import HostError

type Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]
type Opener = Callable[[], Awaitable[Streams]]


if sys.platform != "win32":

    def unix_opener(path: str) -> Opener:
        async def open_unix() -> Streams:
            try:
                return await asyncio.open_unix_connection(path)
            except OSError as error:
                raise HostError("service_unavailable", str(error), retryable=True) from error

        return open_unix

else:
    _ERROR_PIPE_BUSY = 231
    _PIPE_BUSY_ATTEMPTS = 20
    _PIPE_BUSY_DELAY_S = 0.05

    async def _connect_pipe(loop: asyncio.ProactorEventLoop, path: str) -> Streams:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.create_pipe_connection(lambda: protocol, path)
        return reader, asyncio.StreamWriter(transport, protocol, reader, loop)

    def pipe_opener(path: str) -> Opener:
        async def open_pipe() -> Streams:
            loop = asyncio.get_running_loop()
            if not isinstance(loop, asyncio.ProactorEventLoop):
                raise HostError("unsupported_event_loop", "named pipes need ProactorEventLoop")
            for attempt in range(_PIPE_BUSY_ATTEMPTS):
                try:
                    return await _connect_pipe(loop, path)
                except OSError as error:
                    busy = error.winerror == _ERROR_PIPE_BUSY
                    if busy and attempt + 1 < _PIPE_BUSY_ATTEMPTS:
                        await asyncio.sleep(_PIPE_BUSY_DELAY_S)
                        continue
                    raise HostError("service_unavailable", str(error), retryable=True) from error
            raise AssertionError("unreachable")

        return open_pipe
