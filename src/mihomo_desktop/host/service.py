"""TunnelHost over the framed control protocol: the Windows service pipe, or the
development socket on macOS and Linux."""

from __future__ import annotations

import asyncio

from mihomo_desktop.host.base import (
    Connection,
    HostError,
    HostInfo,
    HostStatus,
    LogEntry,
    Proxy,
    ProxyGroup,
    RunOptions,
    TrafficSample,
    TunnelConfig,
)
from mihomo_desktop.host.client import ControlClient
from mihomo_desktop.host.transport import Opener
from mihomo_desktop.protocol import control_pb2

pb = control_pb2

MODES = {"rule": pb.MODE_RULE, "global": pb.MODE_GLOBAL, "direct": pb.MODE_DIRECT}
LOG_LEVELS = {
    "debug": pb.LOG_LEVEL_DEBUG,
    "info": pb.LOG_LEVEL_INFO,
    "warning": pb.LOG_LEVEL_WARNING,
    "error": pb.LOG_LEVEL_ERROR,
}


def _enum_name(enum: type, prefix: str, value: int) -> str:
    return enum.Name(value).removeprefix(prefix).lower()


def _options(options: RunOptions) -> pb.RunOptions:
    return pb.RunOptions(
        tun_enabled=options.tun_enabled,
        system_proxy_enabled=options.system_proxy_enabled,
        mixed_port=options.mixed_port,
        allow_lan=options.allow_lan,
    )


def _group(group: pb.ProxyGroup) -> ProxyGroup:
    return ProxyGroup(
        name=group.name,
        type=group.type,
        now=group.now,
        proxies=tuple(
            Proxy(p.name, p.type, p.delay_ms if p.HasField("delay_ms") else None)
            for p in group.proxies
        ),
    )


def _connection(c: pb.Connection) -> Connection:
    return Connection(
        id=c.id,
        host=c.host,
        network=c.network,
        rule=c.rule if not c.rule_payload else f"{c.rule},{c.rule_payload}",
        # mihomo records the chain from the final proxy outwards.
        chains=tuple(reversed(c.chains)),
        process=c.process,
        upload_bytes=c.upload_bytes,
        download_bytes=c.download_bytes,
        started_at_unix_ms=c.started_at_unix_ms,
        source=c.source,
        destination=c.destination,
    )


class ServiceHost:
    def __init__(self, opener: Opener) -> None:
        self._opener = opener
        self._client: ControlClient | None = None
        self._connect_lock = asyncio.Lock()
        self._subscriptions: dict[str, int] = {}
        self._connections: list[Connection] = []  # the latest snapshot, shared by every poll
        self._log_level = ""  # the level of the logs subscription

    async def _connected(self) -> ControlClient:
        async with self._connect_lock:
            if self._client is None or self._client.closed:
                self._client = await ControlClient.connect(self._opener)
                self._subscriptions.clear()
                self._connections = []
            return self._client

    async def _call(self, request: pb.Request) -> pb.Response:
        return await (await self._connected()).call(request)

    async def get_info(self) -> HostInfo:
        info = (await self._call(pb.Request(get_info=pb.GetInfoRequest()))).get_info
        return HostInfo(
            protocol_version=info.protocol_version,
            service_version=info.service_version,
            core_version=info.core_version,
            state=_enum_name(pb.RunState, "RUN_STATE_", info.state),
            ownership=_enum_name(pb.Ownership, "OWNERSHIP_", info.ownership),
        )

    async def get_status(self) -> HostStatus:
        status = (await self._call(pb.Request(get_status=pb.GetStatusRequest()))).get_status
        return HostStatus(
            state=_enum_name(pb.RunState, "RUN_STATE_", status.state),
            mode=_enum_name(pb.Mode, "MODE_", status.mode),
            started_at_unix_ms=status.started_at_unix_ms,
            memory_bytes=status.memory_bytes,
            connection_count=status.connection_count,
            tun_enabled=status.tun_enabled,
            system_proxy_enabled=status.system_proxy_enabled,
        )

    async def validate(self, config: TunnelConfig) -> None:
        request = pb.ValidateRequest(
            config_yaml=config.config_yaml, overrides=list(config.overrides)
        )
        await self._call(pb.Request(validate=request))

    async def start(self, config: TunnelConfig) -> None:
        request = pb.StartRequest(
            config_yaml=config.config_yaml,
            overrides=list(config.overrides),
            options=_options(config.options),
        )
        await self._call(pb.Request(start=request))

    async def reload(self, config: TunnelConfig) -> None:
        request = pb.ReloadRequest(
            config_yaml=config.config_yaml,
            overrides=list(config.overrides),
            options=_options(config.options),
        )
        await self._call(pb.Request(reload=request))

    async def stop(self) -> None:
        await self._call(pb.Request(stop=pb.StopRequest()))

    async def set_mode(self, mode: str) -> None:
        await self._call(pb.Request(set_mode=pb.SetModeRequest(mode=MODES[mode])))

    async def set_tun(self, enabled: bool) -> None:
        # Toggling TUN is a Reload with new RunOptions; the settings page (roadmap M3)
        # owns RunOptions and will send that Reload.
        raise HostError("not_implemented", "changing TUN goes through the settings page")

    async def set_system_proxy(self, enabled: bool) -> None:
        request = pb.Request(set_system_proxy=pb.SetSystemProxyRequest(enabled=enabled))
        await self._call(request)

    async def list_groups(self) -> list[ProxyGroup]:
        response = await self._call(pb.Request(list_groups=pb.ListGroupsRequest()))
        return [_group(g) for g in response.list_groups.groups]

    async def select_proxy(self, group: str, proxy: str) -> None:
        request = pb.Request(select_proxy=pb.SelectProxyRequest(group=group, proxy=proxy))
        await self._call(request)

    async def health_check(self, group: str) -> None:
        await self._call(pb.Request(health_check=pb.HealthCheckRequest(group=group)))

    async def _poll(self, topic: str, subscribe: pb.Request) -> list[pb.Event]:
        """Drain a subscription, re-subscribing once if the service expired it."""
        for attempt in range(2):
            if topic not in self._subscriptions:
                response = await self._call(subscribe)
                self._subscriptions[topic] = response.subscribe.subscription_id
            request = pb.Request(
                poll_events=pb.PollEventsRequest(subscription_id=self._subscriptions[topic])
            )
            try:
                return list((await self._call(request)).poll_events.events)
            except HostError as error:
                if error.code != "unknown_subscription" or attempt:
                    raise
                del self._subscriptions[topic]
        raise AssertionError("unreachable")

    async def poll_traffic(self) -> list[TrafficSample]:
        events = await self._poll(
            "traffic", pb.Request(subscribe_traffic=pb.SubscribeTrafficRequest())
        )
        return [
            TrafficSample(
                e.traffic.up_bytes_per_second,
                e.traffic.down_bytes_per_second,
                e.traffic.up_total_bytes,
                e.traffic.down_total_bytes,
            )
            for e in events
            if e.HasField("traffic")
        ]

    async def poll_connections(self) -> list[Connection]:
        events = await self._poll(
            "connections", pb.Request(subscribe_connections=pb.SubscribeConnectionsRequest())
        )
        snapshots = [e.connections for e in events if e.HasField("connections")]
        if snapshots:
            self._connections = [_connection(c) for c in snapshots[-1].connections]
        return list(self._connections)

    async def close_connection(self, connection_id: str) -> None:
        request = pb.Request(close_connection=pb.CloseConnectionRequest(id=connection_id))
        try:
            await self._call(request)
        except HostError as error:
            # The core answers invalid_request for an id it no longer tracks.
            if error.code != "invalid_request":
                raise

    async def close_all_connections(self) -> None:
        await self._call(pb.Request(close_all_connections=pb.CloseAllConnectionsRequest()))

    async def poll_logs(self, level: str) -> list[LogEntry]:
        if level != self._log_level and "logs" in self._subscriptions:
            await self._unsubscribe("logs")
        self._log_level = level
        subscribe = pb.SubscribeLogsRequest(level=LOG_LEVELS[level])
        events = await self._poll("logs", pb.Request(subscribe_logs=subscribe))
        return [
            LogEntry(
                level=_enum_name(pb.LogLevel, "LOG_LEVEL_", e.log.level),
                message=e.log.message,
                time_unix_ms=e.log.time_unix_ms,
            )
            for e in events
            if e.HasField("log")
        ]

    async def _unsubscribe(self, topic: str) -> None:
        """End a subscription now rather than leave it to expire; one that already expired
        is fine."""
        request = pb.UnsubscribeRequest(subscription_id=self._subscriptions.pop(topic))
        try:
            await self._call(pb.Request(unsubscribe=request))
        except HostError as error:
            if error.code != "unknown_subscription":
                raise

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
