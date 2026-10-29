"""TunnelHost over the framed control protocol: the Windows service pipe, or the
development socket on macOS and Linux."""

from __future__ import annotations

import asyncio

from mihomo_desktop.host.base import HostInfo
from mihomo_desktop.host.client import ControlClient
from mihomo_desktop.host.transport import Opener
from mihomo_desktop.protocol import control_pb2


def _enum_name(enum: type, prefix: str, value: int) -> str:
    return enum.Name(value).removeprefix(prefix).lower()


class ServiceHost:
    def __init__(self, opener: Opener) -> None:
        self._opener = opener
        self._client: ControlClient | None = None
        self._connect_lock = asyncio.Lock()

    async def _connected(self) -> ControlClient:
        async with self._connect_lock:
            if self._client is None or self._client.closed:
                self._client = await ControlClient.connect(self._opener)
            return self._client

    async def get_info(self) -> HostInfo:
        client = await self._connected()
        response = await client.call(control_pb2.Request(get_info=control_pb2.GetInfoRequest()))
        info = response.get_info
        return HostInfo(
            protocol_version=info.protocol_version,
            service_version=info.service_version,
            core_version=info.core_version,
            state=_enum_name(control_pb2.RunState, "RUN_STATE_", info.state),
            ownership=_enum_name(control_pb2.Ownership, "OWNERSHIP_", info.ownership),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
