"""TunnelHost over the framed control protocol: the Windows service pipe, or the
development socket on macOS and Linux."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from solaris.host.base import (
    EXPORT_GENERATED,
    EXPORT_SHARE,
    TAKEOVER_SETTINGS,
    ComposedGroup,
    Composition,
    CompositionReport,
    CompositionWarning,
    Connection,
    GeoCategories,
    GeoDataset,
    GroupPreview,
    HostError,
    HostInfo,
    HostStatus,
    LogEntry,
    MeshOutbound,
    PlanConversion,
    PoolNode,
    Proxy,
    ProxyGroup,
    Rule,
    RuleMatch,
    RunOptions,
    SkippedRule,
    SourceInspection,
    SourceNode,
    TrafficSample,
    TunnelConfig,
    WarpAccount,
    bundled_geo_categories,
    taken_over,
)
from solaris.host.client import ControlClient
from solaris.host.transport import Opener
from solaris.protocol import control_pb2

pb = control_pb2

MODES = {"rule": pb.MODE_RULE, "global": pb.MODE_GLOBAL, "direct": pb.MODE_DIRECT}
EXPORT_FORMATS = {
    EXPORT_SHARE: pb.EXPORT_FORMAT_SHARE,
    EXPORT_GENERATED: pb.EXPORT_FORMAT_GENERATED,
}
WARP_PROTOCOLS = {
    "masque-h2": pb.WARP_PROTOCOL_MASQUE_H2,
    "masque-h3": pb.WARP_PROTOCOL_MASQUE_H3,
    "wireguard": pb.WARP_PROTOCOL_WIREGUARD,
}
LOG_LEVELS = {
    "debug": pb.LOG_LEVEL_DEBUG,
    "info": pb.LOG_LEVEL_INFO,
    "warning": pb.LOG_LEVEL_WARNING,
    "error": pb.LOG_LEVEL_ERROR,
}
# The Windows service answers not_claimed while it is free but not ours (docs/architecture.md
# §5.4). These requests only show what the core does, so they answer as for a stopped core
# instead of claiming it: two signed-in users' windows would otherwise take it from each
# other back and forth. Everything else claims the service first.
_READS = frozenset(
    {
        "get_status",
        "list_groups",
        "list_rules",
        "list_geo_categories",
        "subscribe_logs",
        "subscribe_traffic",
        "subscribe_connections",
        "poll_events",
        "unsubscribe",
        "stop",
        "close_connection",
        "close_all_connections",
    }
)
# A claim that changes the owner restarts the service, which opens their home; these codes
# mean it is on its way back.
_RESTARTING = frozenset({"service_restarting", "connection_closed", "service_unavailable"})
CLAIM_TIMEOUT_S = 20.0
CLAIM_RETRY_S = 0.5
IDLE_STATUS = HostStatus("stopped", "rule", 0, 0, 0, False, False)


def _enum_name(enum: type, prefix: str, value: int) -> str:
    return enum.Name(value).removeprefix(prefix).lower()


TUN_STACKS = {
    "": pb.TUN_STACK_UNSPECIFIED,
    "system": pb.TUN_STACK_SYSTEM,
    "mips": pb.TUN_STACK_MIPS,
    "gvisor": pb.TUN_STACK_GVISOR,
    "mixed": pb.TUN_STACK_MIXED,
}


def _options(options: RunOptions) -> pb.RunOptions:
    return pb.RunOptions(
        tun_enabled=options.tun_enabled,
        system_proxy_enabled=options.system_proxy_enabled,
        mixed_port=options.mixed_port,
        allow_lan=options.allow_lan,
        tun_stack=TUN_STACKS[options.tun_stack],
    )


def _composition(composition: Composition) -> pb.Composition:
    return pb.Composition(
        sources=[
            pb.CompositionSource(id=source.id, name=source.name, yaml=source.yaml)
            for source in composition.sources
        ],
        info_filter=composition.info_filter,
        plan_json=composition.plan_json,
        egress=pb.Egress(
            warp=_warp_account(composition.egress.warp) if composition.egress.warp else None
        ),
    )


def _warp_account(account: WarpAccount) -> pb.WarpAccount:
    return pb.WarpAccount(
        device_id=account.device_id,
        token=account.token,
        protocol=WARP_PROTOCOLS[account.protocol],
        private_key=account.private_key,
        peer_public_key=account.peer_public_key,
        endpoint=account.endpoint,
        address_v4=account.address_v4,
        address_v6=account.address_v6,
        client_id=account.client_id,
        registered_at_unix_ms=account.registered_at_unix_ms,
    )


def _warp_account_from(account: pb.WarpAccount) -> WarpAccount:
    protocol = account.protocol or pb.WARP_PROTOCOL_MASQUE_H2  # unspecified means the default
    return WarpAccount(
        device_id=account.device_id,
        token=account.token,
        protocol=_enum_name(pb.WarpProtocol, "WARP_PROTOCOL_", protocol).replace("_", "-"),
        private_key=account.private_key,
        peer_public_key=account.peer_public_key,
        endpoint=account.endpoint,
        address_v4=account.address_v4,
        address_v6=account.address_v6,
        client_id=account.client_id,
        registered_at_unix_ms=account.registered_at_unix_ms,
    )


def _with_source[R: (pb.ValidateRequest, pb.StartRequest, pb.ReloadRequest, pb.MatchRuleRequest)](
    request: R, config: TunnelConfig
) -> R:
    """Fill the request's oneof source: the composition, or else the YAML."""
    if config.composition is not None:
        request.composition.CopyFrom(_composition(config.composition))
    else:
        request.config_yaml = config.config_yaml
    return request


def _report(response: pb.Response) -> CompositionReport | None:
    if response.WhichOneof("result") != "composition":
        return None
    report = response.composition
    return CompositionReport(
        warnings=tuple(CompositionWarning(w.entry_id, w.reason, w.target) for w in report.warnings),
        groups=tuple(
            ComposedGroup(
                g.id,
                g.name,
                _enum_name(pb.GroupKind, "GROUP_KIND_", g.kind),
                g.region,
                g.node_count,
            )
            for g in report.groups
        ),
        rules=tuple(_rule(r) for r in report.rules),
        nodes=tuple(_pool_node(n) for n in report.nodes),
        mesh=tuple(MeshOutbound(m.id, m.name, m.type, m.source_id) for m in report.mesh),
    )


def _rule(rule: pb.Rule) -> Rule:
    return Rule(rule.type, rule.payload, rule.proxy, rule.entry_id)


def _pool_node(node: pb.PoolNode) -> PoolNode:
    return PoolNode(node.name, node.original_name, node.source_id, node.type, node.region)


def _dataset(categories: pb.GeoCategories, bundled: GeoDataset) -> GeoDataset:
    if not categories.names:
        return bundled
    return GeoDataset(
        tuple(categories.names), categories.sha256, categories.updated_at_unix_ms, False
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
        rule_entry_id=c.rule_entry_id,
    )


class ServiceHost:
    def __init__(self, opener: Opener, *, takeover: str = TAKEOVER_SETTINGS) -> None:
        """``takeover`` is TAKEOVER_NONE for the development service."""
        self._opener = opener
        self.takeover = takeover
        self._client: ControlClient | None = None
        self._connect_lock = asyncio.Lock()
        self._subscriptions: dict[str, int] = {}
        self._connections: list[Connection] = []  # the latest snapshot, shared by every poll
        self._log_level = ""  # the level of the logs subscription

    def _options(self, options: RunOptions) -> pb.RunOptions:
        return _options(taken_over(options, self.takeover))

    async def _connected(self) -> ControlClient:
        async with self._connect_lock:
            if self._client is None or self._client.closed:
                self._client = await ControlClient.connect(self._opener)
                self._subscriptions.clear()
                self._connections = []
            return self._client

    async def _call(self, request: pb.Request) -> pb.Response:
        try:
            return await (await self._connected()).call(request)
        except HostError as error:
            if error.code != "not_claimed" or request.WhichOneof("method") in _READS:
                raise
        await self.claim()
        return await (await self._connected()).call(request)

    async def claim(self) -> None:
        """Become the service's owner: it is free, or ours already. A service that has to
        open another user's home restarts first, and is claimed again once it is back."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CLAIM_TIMEOUT_S
        while True:
            try:
                await (await self._connected()).call(pb.Request(claim=pb.ClaimRequest()))
                return
            except HostError as error:
                if error.code not in _RESTARTING or loop.time() >= deadline:
                    raise
            await self.close()
            await asyncio.sleep(CLAIM_RETRY_S)

    @staticmethod
    async def _unclaimed[T](read: Awaitable[T], idle: T) -> T:
        """``read``'s answer, or ``idle`` while the service is free but not ours."""
        try:
            return await read
        except HostError as error:
            if error.code != "not_claimed":
                raise
            return idle

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
        response = self._call(pb.Request(get_status=pb.GetStatusRequest()))
        answered = await self._unclaimed(response, None)
        if answered is None:
            return IDLE_STATUS
        status = answered.get_status
        return HostStatus(
            state=_enum_name(pb.RunState, "RUN_STATE_", status.state),
            mode=_enum_name(pb.Mode, "MODE_", status.mode),
            started_at_unix_ms=status.started_at_unix_ms,
            memory_bytes=status.memory_bytes,
            connection_count=status.connection_count,
            tun_enabled=status.tun_enabled,
            system_proxy_enabled=status.system_proxy_enabled,
        )

    async def validate(self, config: TunnelConfig) -> CompositionReport | None:
        request = _with_source(pb.ValidateRequest(overrides=list(config.overrides)), config)
        return _report(await self._call(pb.Request(validate=request)))

    async def start(self, config: TunnelConfig) -> CompositionReport | None:
        options = self._options(config.options)
        request = _with_source(
            pb.StartRequest(overrides=list(config.overrides), options=options), config
        )
        return _report(await self._call(pb.Request(start=request)))

    async def reload(self, config: TunnelConfig) -> CompositionReport | None:
        options = self._options(config.options)
        request = _with_source(
            pb.ReloadRequest(overrides=list(config.overrides), options=options), config
        )
        return _report(await self._call(pb.Request(reload=request)))

    async def inspect_source(self, yaml: str, info_filter: str = "") -> SourceInspection:
        request = pb.InspectSourceRequest(yaml=yaml, info_filter=info_filter)
        inspected = (await self._call(pb.Request(inspect_source=request))).inspect_source
        return SourceInspection(
            nodes=tuple(
                SourceNode(n.name, n.type, n.region, n.info, n.mesh) for n in inspected.nodes
            ),
            has_rules=inspected.has_rules,
            group_count=inspected.group_count,
            rule_count=inspected.rule_count,
        )

    async def list_geo_categories(self) -> GeoCategories:
        request = pb.Request(list_geo_categories=pb.ListGeoCategoriesRequest())
        bundled = bundled_geo_categories()
        answered = await self._unclaimed(self._call(request), None)
        if answered is None:
            return bundled
        listed = answered.list_geo_categories
        return GeoCategories(
            geosite=_dataset(listed.geosite, bundled.geosite),
            geoip=_dataset(listed.geoip, bundled.geoip),
        )

    async def preview_group(self, composition: Composition, group_id: str) -> GroupPreview:
        request = pb.PreviewGroupRequest(composition=_composition(composition), group_id=group_id)
        preview = (await self._call(pb.Request(preview_group=request))).preview_group
        return GroupPreview(tuple(_pool_node(n) for n in preview.nodes), tuple(preview.others))

    async def match_rule(
        self, host: str, port: int = 0, network: str = "tcp", config: TunnelConfig | None = None
    ) -> RuleMatch:
        request = pb.MatchRuleRequest(host=host, port=port, network=network)
        if config is not None:
            request.overrides.extend(config.overrides)
            _with_source(request, config)
        matched = (await self._call(pb.Request(match_rule=request))).match_rule
        return RuleMatch(
            matched=matched.matched,
            index=matched.index,
            rule=_rule(matched.rule) if matched.HasField("rule") else None,
            # mihomo's order is from the node outwards, as for connections.
            chain=tuple(reversed(matched.chain)),
            resolved_ip=matched.resolved_ip,
        )

    async def convert_to_plan(
        self, yaml: str, *, source_id: str = "", locale: str = "zh", info_filter: str = ""
    ) -> PlanConversion:
        request = pb.ConvertToPlanRequest(
            yaml=yaml, source_id=source_id, locale=locale, info_filter=info_filter
        )
        converted = (await self._call(pb.Request(convert_to_plan=request))).convert_to_plan
        return PlanConversion(
            plan_json=converted.plan_json,
            group_count=converted.group_count,
            merged_group_count=converted.merged_group_count,
            rule_count=converted.rule_count,
            raw_rule_count=converted.raw_rule_count,
            entry_count=converted.entry_count,
            skipped=tuple(SkippedRule(r.rule, r.reason) for r in converted.skipped),
        )

    async def export_plan(self, config: TunnelConfig, export: str = EXPORT_SHARE) -> str:
        if config.composition is None:
            raise ValueError("export_plan needs a composition")
        request = pb.ExportPlanRequest(
            composition=_composition(config.composition),
            format=EXPORT_FORMATS[export],
            overrides=list(config.overrides),
            options=self._options(config.options),
        )
        return (await self._call(pb.Request(export_plan=request))).export_plan.yaml

    async def register_warp(
        self, via: str, protocol: str = "masque-h2", existing: WarpAccount | None = None
    ) -> WarpAccount:
        request = pb.RegisterWarpRequest(
            via=via,
            protocol=WARP_PROTOCOLS[protocol],
            device_id=existing.device_id if existing else "",
            token=existing.token if existing else "",
        )
        response = await self._call(pb.Request(register_warp=request))
        return _warp_account_from(response.register_warp.account)

    async def stop(self) -> None:
        await self._unclaimed(self._call(pb.Request(stop=pb.StopRequest())), None)

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
        response = self._call(pb.Request(list_groups=pb.ListGroupsRequest()))
        answered = await self._unclaimed(response, None)
        return [_group(g) for g in answered.list_groups.groups] if answered else []

    async def list_rules(self) -> list[Rule]:
        response = self._call(pb.Request(list_rules=pb.ListRulesRequest()))
        answered = await self._unclaimed(response, None)
        return [_rule(r) for r in answered.list_rules.rules] if answered else []

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
        subscribe = pb.Request(subscribe_traffic=pb.SubscribeTrafficRequest())
        events = await self._unclaimed(self._poll("traffic", subscribe), [])
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
        subscribe = pb.Request(subscribe_connections=pb.SubscribeConnectionsRequest())
        events = await self._unclaimed(self._poll("connections", subscribe), [])
        snapshots = [e.connections for e in events if e.HasField("connections")]
        if snapshots:
            self._connections = [_connection(c) for c in snapshots[-1].connections]
        return list(self._connections)

    async def close_connection(self, connection_id: str) -> None:
        request = pb.Request(close_connection=pb.CloseConnectionRequest(id=connection_id))
        try:
            await self._unclaimed(self._call(request), None)
        except HostError as error:
            # The core answers invalid_request for an id it no longer tracks.
            if error.code != "invalid_request":
                raise

    async def close_all_connections(self) -> None:
        request = pb.Request(close_all_connections=pb.CloseAllConnectionsRequest())
        await self._unclaimed(self._call(request), None)

    async def poll_logs(self, level: str) -> list[LogEntry]:
        if level != self._log_level and "logs" in self._subscriptions:
            await self._unsubscribe("logs")
        self._log_level = level
        subscribe = pb.SubscribeLogsRequest(level=LOG_LEVELS[level])
        events = await self._unclaimed(self._poll("logs", pb.Request(subscribe_logs=subscribe)), [])
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
