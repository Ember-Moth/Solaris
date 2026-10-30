from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RunState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RUN_STATE_UNSPECIFIED: _ClassVar[RunState]
    RUN_STATE_STOPPED: _ClassVar[RunState]
    RUN_STATE_STARTING: _ClassVar[RunState]
    RUN_STATE_RUNNING: _ClassVar[RunState]
    RUN_STATE_STOPPING: _ClassVar[RunState]
    RUN_STATE_FAILED: _ClassVar[RunState]

class Ownership(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OWNERSHIP_UNSPECIFIED: _ClassVar[Ownership]
    OWNERSHIP_AVAILABLE: _ClassVar[Ownership]
    OWNERSHIP_CALLER: _ClassVar[Ownership]
    OWNERSHIP_OTHER: _ClassVar[Ownership]

class TunStack(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TUN_STACK_UNSPECIFIED: _ClassVar[TunStack]
    TUN_STACK_SYSTEM: _ClassVar[TunStack]
    TUN_STACK_MIPS: _ClassVar[TunStack]
    TUN_STACK_GVISOR: _ClassVar[TunStack]
    TUN_STACK_MIXED: _ClassVar[TunStack]

class WarpProtocol(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WARP_PROTOCOL_UNSPECIFIED: _ClassVar[WarpProtocol]
    WARP_PROTOCOL_MASQUE_H2: _ClassVar[WarpProtocol]
    WARP_PROTOCOL_MASQUE_H3: _ClassVar[WarpProtocol]
    WARP_PROTOCOL_WIREGUARD: _ClassVar[WarpProtocol]

class GroupKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GROUP_KIND_UNSPECIFIED: _ClassVar[GroupKind]
    GROUP_KIND_BUILTIN: _ClassVar[GroupKind]
    GROUP_KIND_REGION: _ClassVar[GroupKind]
    GROUP_KIND_CUSTOM: _ClassVar[GroupKind]

class Mode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[Mode]
    MODE_RULE: _ClassVar[Mode]
    MODE_GLOBAL: _ClassVar[Mode]
    MODE_DIRECT: _ClassVar[Mode]

class GroupSort(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GROUP_SORT_UNSPECIFIED: _ClassVar[GroupSort]
    GROUP_SORT_NAME: _ClassVar[GroupSort]
    GROUP_SORT_DELAY: _ClassVar[GroupSort]

class ProviderKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PROVIDER_KIND_UNSPECIFIED: _ClassVar[ProviderKind]
    PROVIDER_KIND_PROXY: _ClassVar[ProviderKind]
    PROVIDER_KIND_RULE: _ClassVar[ProviderKind]

class LogLevel(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    LOG_LEVEL_UNSPECIFIED: _ClassVar[LogLevel]
    LOG_LEVEL_DEBUG: _ClassVar[LogLevel]
    LOG_LEVEL_INFO: _ClassVar[LogLevel]
    LOG_LEVEL_WARNING: _ClassVar[LogLevel]
    LOG_LEVEL_ERROR: _ClassVar[LogLevel]

class ExportFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXPORT_FORMAT_UNSPECIFIED: _ClassVar[ExportFormat]
    EXPORT_FORMAT_SHARE: _ClassVar[ExportFormat]
    EXPORT_FORMAT_GENERATED: _ClassVar[ExportFormat]
RUN_STATE_UNSPECIFIED: RunState
RUN_STATE_STOPPED: RunState
RUN_STATE_STARTING: RunState
RUN_STATE_RUNNING: RunState
RUN_STATE_STOPPING: RunState
RUN_STATE_FAILED: RunState
OWNERSHIP_UNSPECIFIED: Ownership
OWNERSHIP_AVAILABLE: Ownership
OWNERSHIP_CALLER: Ownership
OWNERSHIP_OTHER: Ownership
TUN_STACK_UNSPECIFIED: TunStack
TUN_STACK_SYSTEM: TunStack
TUN_STACK_MIPS: TunStack
TUN_STACK_GVISOR: TunStack
TUN_STACK_MIXED: TunStack
WARP_PROTOCOL_UNSPECIFIED: WarpProtocol
WARP_PROTOCOL_MASQUE_H2: WarpProtocol
WARP_PROTOCOL_MASQUE_H3: WarpProtocol
WARP_PROTOCOL_WIREGUARD: WarpProtocol
GROUP_KIND_UNSPECIFIED: GroupKind
GROUP_KIND_BUILTIN: GroupKind
GROUP_KIND_REGION: GroupKind
GROUP_KIND_CUSTOM: GroupKind
MODE_UNSPECIFIED: Mode
MODE_RULE: Mode
MODE_GLOBAL: Mode
MODE_DIRECT: Mode
GROUP_SORT_UNSPECIFIED: GroupSort
GROUP_SORT_NAME: GroupSort
GROUP_SORT_DELAY: GroupSort
PROVIDER_KIND_UNSPECIFIED: ProviderKind
PROVIDER_KIND_PROXY: ProviderKind
PROVIDER_KIND_RULE: ProviderKind
LOG_LEVEL_UNSPECIFIED: LogLevel
LOG_LEVEL_DEBUG: LogLevel
LOG_LEVEL_INFO: LogLevel
LOG_LEVEL_WARNING: LogLevel
LOG_LEVEL_ERROR: LogLevel
EXPORT_FORMAT_UNSPECIFIED: ExportFormat
EXPORT_FORMAT_SHARE: ExportFormat
EXPORT_FORMAT_GENERATED: ExportFormat

class Request(_message.Message):
    __slots__ = ("protocol_version", "id", "get_info", "claim", "validate", "start", "reload", "stop", "set_system_proxy", "get_status", "set_mode", "list_groups", "get_group", "select_proxy", "health_check", "test_delay", "list_providers", "update_provider", "list_rules", "close_connection", "close_all_connections", "flush_fake_ip", "flush_dns_cache", "subscribe_logs", "subscribe_traffic", "subscribe_connections", "poll_events", "unsubscribe", "inspect_source", "list_geo_categories", "preview_group", "match_rule", "convert_to_plan", "export_plan", "register_warp")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    GET_INFO_FIELD_NUMBER: _ClassVar[int]
    CLAIM_FIELD_NUMBER: _ClassVar[int]
    VALIDATE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    RELOAD_FIELD_NUMBER: _ClassVar[int]
    STOP_FIELD_NUMBER: _ClassVar[int]
    SET_SYSTEM_PROXY_FIELD_NUMBER: _ClassVar[int]
    GET_STATUS_FIELD_NUMBER: _ClassVar[int]
    SET_MODE_FIELD_NUMBER: _ClassVar[int]
    LIST_GROUPS_FIELD_NUMBER: _ClassVar[int]
    GET_GROUP_FIELD_NUMBER: _ClassVar[int]
    SELECT_PROXY_FIELD_NUMBER: _ClassVar[int]
    HEALTH_CHECK_FIELD_NUMBER: _ClassVar[int]
    TEST_DELAY_FIELD_NUMBER: _ClassVar[int]
    LIST_PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    UPDATE_PROVIDER_FIELD_NUMBER: _ClassVar[int]
    LIST_RULES_FIELD_NUMBER: _ClassVar[int]
    CLOSE_CONNECTION_FIELD_NUMBER: _ClassVar[int]
    CLOSE_ALL_CONNECTIONS_FIELD_NUMBER: _ClassVar[int]
    FLUSH_FAKE_IP_FIELD_NUMBER: _ClassVar[int]
    FLUSH_DNS_CACHE_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_LOGS_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_TRAFFIC_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_CONNECTIONS_FIELD_NUMBER: _ClassVar[int]
    POLL_EVENTS_FIELD_NUMBER: _ClassVar[int]
    UNSUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    INSPECT_SOURCE_FIELD_NUMBER: _ClassVar[int]
    LIST_GEO_CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_GROUP_FIELD_NUMBER: _ClassVar[int]
    MATCH_RULE_FIELD_NUMBER: _ClassVar[int]
    CONVERT_TO_PLAN_FIELD_NUMBER: _ClassVar[int]
    EXPORT_PLAN_FIELD_NUMBER: _ClassVar[int]
    REGISTER_WARP_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    id: int
    get_info: GetInfoRequest
    claim: ClaimRequest
    validate: ValidateRequest
    start: StartRequest
    reload: ReloadRequest
    stop: StopRequest
    set_system_proxy: SetSystemProxyRequest
    get_status: GetStatusRequest
    set_mode: SetModeRequest
    list_groups: ListGroupsRequest
    get_group: GetGroupRequest
    select_proxy: SelectProxyRequest
    health_check: HealthCheckRequest
    test_delay: TestDelayRequest
    list_providers: ListProvidersRequest
    update_provider: UpdateProviderRequest
    list_rules: ListRulesRequest
    close_connection: CloseConnectionRequest
    close_all_connections: CloseAllConnectionsRequest
    flush_fake_ip: FlushFakeIPRequest
    flush_dns_cache: FlushDNSCacheRequest
    subscribe_logs: SubscribeLogsRequest
    subscribe_traffic: SubscribeTrafficRequest
    subscribe_connections: SubscribeConnectionsRequest
    poll_events: PollEventsRequest
    unsubscribe: UnsubscribeRequest
    inspect_source: InspectSourceRequest
    list_geo_categories: ListGeoCategoriesRequest
    preview_group: PreviewGroupRequest
    match_rule: MatchRuleRequest
    convert_to_plan: ConvertToPlanRequest
    export_plan: ExportPlanRequest
    register_warp: RegisterWarpRequest
    def __init__(self, protocol_version: _Optional[int] = ..., id: _Optional[int] = ..., get_info: _Optional[_Union[GetInfoRequest, _Mapping]] = ..., claim: _Optional[_Union[ClaimRequest, _Mapping]] = ..., validate: _Optional[_Union[ValidateRequest, _Mapping]] = ..., start: _Optional[_Union[StartRequest, _Mapping]] = ..., reload: _Optional[_Union[ReloadRequest, _Mapping]] = ..., stop: _Optional[_Union[StopRequest, _Mapping]] = ..., set_system_proxy: _Optional[_Union[SetSystemProxyRequest, _Mapping]] = ..., get_status: _Optional[_Union[GetStatusRequest, _Mapping]] = ..., set_mode: _Optional[_Union[SetModeRequest, _Mapping]] = ..., list_groups: _Optional[_Union[ListGroupsRequest, _Mapping]] = ..., get_group: _Optional[_Union[GetGroupRequest, _Mapping]] = ..., select_proxy: _Optional[_Union[SelectProxyRequest, _Mapping]] = ..., health_check: _Optional[_Union[HealthCheckRequest, _Mapping]] = ..., test_delay: _Optional[_Union[TestDelayRequest, _Mapping]] = ..., list_providers: _Optional[_Union[ListProvidersRequest, _Mapping]] = ..., update_provider: _Optional[_Union[UpdateProviderRequest, _Mapping]] = ..., list_rules: _Optional[_Union[ListRulesRequest, _Mapping]] = ..., close_connection: _Optional[_Union[CloseConnectionRequest, _Mapping]] = ..., close_all_connections: _Optional[_Union[CloseAllConnectionsRequest, _Mapping]] = ..., flush_fake_ip: _Optional[_Union[FlushFakeIPRequest, _Mapping]] = ..., flush_dns_cache: _Optional[_Union[FlushDNSCacheRequest, _Mapping]] = ..., subscribe_logs: _Optional[_Union[SubscribeLogsRequest, _Mapping]] = ..., subscribe_traffic: _Optional[_Union[SubscribeTrafficRequest, _Mapping]] = ..., subscribe_connections: _Optional[_Union[SubscribeConnectionsRequest, _Mapping]] = ..., poll_events: _Optional[_Union[PollEventsRequest, _Mapping]] = ..., unsubscribe: _Optional[_Union[UnsubscribeRequest, _Mapping]] = ..., inspect_source: _Optional[_Union[InspectSourceRequest, _Mapping]] = ..., list_geo_categories: _Optional[_Union[ListGeoCategoriesRequest, _Mapping]] = ..., preview_group: _Optional[_Union[PreviewGroupRequest, _Mapping]] = ..., match_rule: _Optional[_Union[MatchRuleRequest, _Mapping]] = ..., convert_to_plan: _Optional[_Union[ConvertToPlanRequest, _Mapping]] = ..., export_plan: _Optional[_Union[ExportPlanRequest, _Mapping]] = ..., register_warp: _Optional[_Union[RegisterWarpRequest, _Mapping]] = ...) -> None: ...

class Response(_message.Message):
    __slots__ = ("protocol_version", "id", "error", "empty", "get_info", "composition", "get_status", "list_groups", "get_group", "test_delay", "list_providers", "list_rules", "subscribe", "poll_events", "inspect_source", "list_geo_categories", "preview_group", "match_rule", "convert_to_plan", "export_plan", "register_warp")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    EMPTY_FIELD_NUMBER: _ClassVar[int]
    GET_INFO_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    GET_STATUS_FIELD_NUMBER: _ClassVar[int]
    LIST_GROUPS_FIELD_NUMBER: _ClassVar[int]
    GET_GROUP_FIELD_NUMBER: _ClassVar[int]
    TEST_DELAY_FIELD_NUMBER: _ClassVar[int]
    LIST_PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    LIST_RULES_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    POLL_EVENTS_FIELD_NUMBER: _ClassVar[int]
    INSPECT_SOURCE_FIELD_NUMBER: _ClassVar[int]
    LIST_GEO_CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_GROUP_FIELD_NUMBER: _ClassVar[int]
    MATCH_RULE_FIELD_NUMBER: _ClassVar[int]
    CONVERT_TO_PLAN_FIELD_NUMBER: _ClassVar[int]
    EXPORT_PLAN_FIELD_NUMBER: _ClassVar[int]
    REGISTER_WARP_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    id: int
    error: Error
    empty: Empty
    get_info: GetInfoResponse
    composition: CompositionReport
    get_status: GetStatusResponse
    list_groups: ListGroupsResponse
    get_group: GetGroupResponse
    test_delay: TestDelayResponse
    list_providers: ListProvidersResponse
    list_rules: ListRulesResponse
    subscribe: SubscribeResponse
    poll_events: PollEventsResponse
    inspect_source: InspectSourceResponse
    list_geo_categories: ListGeoCategoriesResponse
    preview_group: PreviewGroupResponse
    match_rule: MatchRuleResponse
    convert_to_plan: ConvertToPlanResponse
    export_plan: ExportPlanResponse
    register_warp: RegisterWarpResponse
    def __init__(self, protocol_version: _Optional[int] = ..., id: _Optional[int] = ..., error: _Optional[_Union[Error, _Mapping]] = ..., empty: _Optional[_Union[Empty, _Mapping]] = ..., get_info: _Optional[_Union[GetInfoResponse, _Mapping]] = ..., composition: _Optional[_Union[CompositionReport, _Mapping]] = ..., get_status: _Optional[_Union[GetStatusResponse, _Mapping]] = ..., list_groups: _Optional[_Union[ListGroupsResponse, _Mapping]] = ..., get_group: _Optional[_Union[GetGroupResponse, _Mapping]] = ..., test_delay: _Optional[_Union[TestDelayResponse, _Mapping]] = ..., list_providers: _Optional[_Union[ListProvidersResponse, _Mapping]] = ..., list_rules: _Optional[_Union[ListRulesResponse, _Mapping]] = ..., subscribe: _Optional[_Union[SubscribeResponse, _Mapping]] = ..., poll_events: _Optional[_Union[PollEventsResponse, _Mapping]] = ..., inspect_source: _Optional[_Union[InspectSourceResponse, _Mapping]] = ..., list_geo_categories: _Optional[_Union[ListGeoCategoriesResponse, _Mapping]] = ..., preview_group: _Optional[_Union[PreviewGroupResponse, _Mapping]] = ..., match_rule: _Optional[_Union[MatchRuleResponse, _Mapping]] = ..., convert_to_plan: _Optional[_Union[ConvertToPlanResponse, _Mapping]] = ..., export_plan: _Optional[_Union[ExportPlanResponse, _Mapping]] = ..., register_warp: _Optional[_Union[RegisterWarpResponse, _Mapping]] = ...) -> None: ...

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Error(_message.Message):
    __slots__ = ("code", "message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: _Optional[bool] = ...) -> None: ...

class GetInfoRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetInfoResponse(_message.Message):
    __slots__ = ("protocol_version", "service_version", "core_version", "state", "ownership")
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SERVICE_VERSION_FIELD_NUMBER: _ClassVar[int]
    CORE_VERSION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    OWNERSHIP_FIELD_NUMBER: _ClassVar[int]
    protocol_version: int
    service_version: str
    core_version: str
    state: RunState
    ownership: Ownership
    def __init__(self, protocol_version: _Optional[int] = ..., service_version: _Optional[str] = ..., core_version: _Optional[str] = ..., state: _Optional[_Union[RunState, str]] = ..., ownership: _Optional[_Union[Ownership, str]] = ...) -> None: ...

class ClaimRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class RunOptions(_message.Message):
    __slots__ = ("tun_enabled", "system_proxy_enabled", "mixed_port", "allow_lan", "tun_stack")
    TUN_ENABLED_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROXY_ENABLED_FIELD_NUMBER: _ClassVar[int]
    MIXED_PORT_FIELD_NUMBER: _ClassVar[int]
    ALLOW_LAN_FIELD_NUMBER: _ClassVar[int]
    TUN_STACK_FIELD_NUMBER: _ClassVar[int]
    tun_enabled: bool
    system_proxy_enabled: bool
    mixed_port: int
    allow_lan: bool
    tun_stack: TunStack
    def __init__(self, tun_enabled: _Optional[bool] = ..., system_proxy_enabled: _Optional[bool] = ..., mixed_port: _Optional[int] = ..., allow_lan: _Optional[bool] = ..., tun_stack: _Optional[_Union[TunStack, str]] = ...) -> None: ...

class ValidateRequest(_message.Message):
    __slots__ = ("config_yaml", "composition", "overrides")
    CONFIG_YAML_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    config_yaml: str
    composition: Composition
    overrides: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, config_yaml: _Optional[str] = ..., composition: _Optional[_Union[Composition, _Mapping]] = ..., overrides: _Optional[_Iterable[str]] = ...) -> None: ...

class StartRequest(_message.Message):
    __slots__ = ("config_yaml", "composition", "overrides", "options")
    CONFIG_YAML_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    config_yaml: str
    composition: Composition
    overrides: _containers.RepeatedScalarFieldContainer[str]
    options: RunOptions
    def __init__(self, config_yaml: _Optional[str] = ..., composition: _Optional[_Union[Composition, _Mapping]] = ..., overrides: _Optional[_Iterable[str]] = ..., options: _Optional[_Union[RunOptions, _Mapping]] = ...) -> None: ...

class ReloadRequest(_message.Message):
    __slots__ = ("config_yaml", "composition", "overrides", "options")
    CONFIG_YAML_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    config_yaml: str
    composition: Composition
    overrides: _containers.RepeatedScalarFieldContainer[str]
    options: RunOptions
    def __init__(self, config_yaml: _Optional[str] = ..., composition: _Optional[_Union[Composition, _Mapping]] = ..., overrides: _Optional[_Iterable[str]] = ..., options: _Optional[_Union[RunOptions, _Mapping]] = ...) -> None: ...

class Composition(_message.Message):
    __slots__ = ("sources", "info_filter", "plan_json", "egress")
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    INFO_FILTER_FIELD_NUMBER: _ClassVar[int]
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    EGRESS_FIELD_NUMBER: _ClassVar[int]
    sources: _containers.RepeatedCompositeFieldContainer[CompositionSource]
    info_filter: str
    plan_json: str
    egress: Egress
    def __init__(self, sources: _Optional[_Iterable[_Union[CompositionSource, _Mapping]]] = ..., info_filter: _Optional[str] = ..., plan_json: _Optional[str] = ..., egress: _Optional[_Union[Egress, _Mapping]] = ...) -> None: ...

class Egress(_message.Message):
    __slots__ = ("warp",)
    WARP_FIELD_NUMBER: _ClassVar[int]
    warp: WarpAccount
    def __init__(self, warp: _Optional[_Union[WarpAccount, _Mapping]] = ...) -> None: ...

class WarpAccount(_message.Message):
    __slots__ = ("device_id", "token", "protocol", "private_key", "peer_public_key", "endpoint", "address_v4", "address_v6", "client_id", "registered_at_unix_ms")
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    PRIVATE_KEY_FIELD_NUMBER: _ClassVar[int]
    PEER_PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_V4_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_V6_FIELD_NUMBER: _ClassVar[int]
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    device_id: str
    token: str
    protocol: WarpProtocol
    private_key: str
    peer_public_key: str
    endpoint: str
    address_v4: str
    address_v6: str
    client_id: str
    registered_at_unix_ms: int
    def __init__(self, device_id: _Optional[str] = ..., token: _Optional[str] = ..., protocol: _Optional[_Union[WarpProtocol, str]] = ..., private_key: _Optional[str] = ..., peer_public_key: _Optional[str] = ..., endpoint: _Optional[str] = ..., address_v4: _Optional[str] = ..., address_v6: _Optional[str] = ..., client_id: _Optional[str] = ..., registered_at_unix_ms: _Optional[int] = ...) -> None: ...

class CompositionSource(_message.Message):
    __slots__ = ("id", "name", "yaml")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    YAML_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    yaml: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., yaml: _Optional[str] = ...) -> None: ...

class CompositionReport(_message.Message):
    __slots__ = ("warnings", "groups", "rules", "nodes", "mesh")
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    NODES_FIELD_NUMBER: _ClassVar[int]
    MESH_FIELD_NUMBER: _ClassVar[int]
    warnings: _containers.RepeatedCompositeFieldContainer[CompositionWarning]
    groups: _containers.RepeatedCompositeFieldContainer[ComposedGroup]
    rules: _containers.RepeatedCompositeFieldContainer[Rule]
    nodes: _containers.RepeatedCompositeFieldContainer[PoolNode]
    mesh: _containers.RepeatedCompositeFieldContainer[MeshOutbound]
    def __init__(self, warnings: _Optional[_Iterable[_Union[CompositionWarning, _Mapping]]] = ..., groups: _Optional[_Iterable[_Union[ComposedGroup, _Mapping]]] = ..., rules: _Optional[_Iterable[_Union[Rule, _Mapping]]] = ..., nodes: _Optional[_Iterable[_Union[PoolNode, _Mapping]]] = ..., mesh: _Optional[_Iterable[_Union[MeshOutbound, _Mapping]]] = ...) -> None: ...

class MeshOutbound(_message.Message):
    __slots__ = ("id", "name", "type", "source_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    type: str
    source_id: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., type: _Optional[str] = ..., source_id: _Optional[str] = ...) -> None: ...

class CompositionWarning(_message.Message):
    __slots__ = ("entry_id", "reason", "target")
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    entry_id: str
    reason: str
    target: str
    def __init__(self, entry_id: _Optional[str] = ..., reason: _Optional[str] = ..., target: _Optional[str] = ...) -> None: ...

class ComposedGroup(_message.Message):
    __slots__ = ("id", "name", "kind", "region", "node_count")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    NODE_COUNT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    kind: GroupKind
    region: str
    node_count: int
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[_Union[GroupKind, str]] = ..., region: _Optional[str] = ..., node_count: _Optional[int] = ...) -> None: ...

class PoolNode(_message.Message):
    __slots__ = ("name", "original_name", "source_id", "type", "region")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_NAME_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    name: str
    original_name: str
    source_id: str
    type: str
    region: str
    def __init__(self, name: _Optional[str] = ..., original_name: _Optional[str] = ..., source_id: _Optional[str] = ..., type: _Optional[str] = ..., region: _Optional[str] = ...) -> None: ...

class StopRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SetSystemProxyRequest(_message.Message):
    __slots__ = ("enabled",)
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    enabled: bool
    def __init__(self, enabled: _Optional[bool] = ...) -> None: ...

class GetStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GetStatusResponse(_message.Message):
    __slots__ = ("state", "mode", "started_at_unix_ms", "memory_bytes", "connection_count", "last_error", "tun_enabled", "system_proxy_enabled")
    STATE_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_COUNT_FIELD_NUMBER: _ClassVar[int]
    LAST_ERROR_FIELD_NUMBER: _ClassVar[int]
    TUN_ENABLED_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROXY_ENABLED_FIELD_NUMBER: _ClassVar[int]
    state: RunState
    mode: Mode
    started_at_unix_ms: int
    memory_bytes: int
    connection_count: int
    last_error: Error
    tun_enabled: bool
    system_proxy_enabled: bool
    def __init__(self, state: _Optional[_Union[RunState, str]] = ..., mode: _Optional[_Union[Mode, str]] = ..., started_at_unix_ms: _Optional[int] = ..., memory_bytes: _Optional[int] = ..., connection_count: _Optional[int] = ..., last_error: _Optional[_Union[Error, _Mapping]] = ..., tun_enabled: _Optional[bool] = ..., system_proxy_enabled: _Optional[bool] = ...) -> None: ...

class SetModeRequest(_message.Message):
    __slots__ = ("mode",)
    MODE_FIELD_NUMBER: _ClassVar[int]
    mode: Mode
    def __init__(self, mode: _Optional[_Union[Mode, str]] = ...) -> None: ...

class Proxy(_message.Message):
    __slots__ = ("name", "type", "delay_ms")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DELAY_MS_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    delay_ms: int
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., delay_ms: _Optional[int] = ...) -> None: ...

class ProxyGroup(_message.Message):
    __slots__ = ("name", "type", "now", "test_url", "proxies")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    NOW_FIELD_NUMBER: _ClassVar[int]
    TEST_URL_FIELD_NUMBER: _ClassVar[int]
    PROXIES_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    now: str
    test_url: str
    proxies: _containers.RepeatedCompositeFieldContainer[Proxy]
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., now: _Optional[str] = ..., test_url: _Optional[str] = ..., proxies: _Optional[_Iterable[_Union[Proxy, _Mapping]]] = ...) -> None: ...

class ListGroupsRequest(_message.Message):
    __slots__ = ("sort", "exclude_not_selectable")
    SORT_FIELD_NUMBER: _ClassVar[int]
    EXCLUDE_NOT_SELECTABLE_FIELD_NUMBER: _ClassVar[int]
    sort: GroupSort
    exclude_not_selectable: bool
    def __init__(self, sort: _Optional[_Union[GroupSort, str]] = ..., exclude_not_selectable: _Optional[bool] = ...) -> None: ...

class ListGroupsResponse(_message.Message):
    __slots__ = ("groups",)
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    groups: _containers.RepeatedCompositeFieldContainer[ProxyGroup]
    def __init__(self, groups: _Optional[_Iterable[_Union[ProxyGroup, _Mapping]]] = ...) -> None: ...

class GetGroupRequest(_message.Message):
    __slots__ = ("name", "sort")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SORT_FIELD_NUMBER: _ClassVar[int]
    name: str
    sort: GroupSort
    def __init__(self, name: _Optional[str] = ..., sort: _Optional[_Union[GroupSort, str]] = ...) -> None: ...

class GetGroupResponse(_message.Message):
    __slots__ = ("group",)
    GROUP_FIELD_NUMBER: _ClassVar[int]
    group: ProxyGroup
    def __init__(self, group: _Optional[_Union[ProxyGroup, _Mapping]] = ...) -> None: ...

class SelectProxyRequest(_message.Message):
    __slots__ = ("group", "proxy")
    GROUP_FIELD_NUMBER: _ClassVar[int]
    PROXY_FIELD_NUMBER: _ClassVar[int]
    group: str
    proxy: str
    def __init__(self, group: _Optional[str] = ..., proxy: _Optional[str] = ...) -> None: ...

class HealthCheckRequest(_message.Message):
    __slots__ = ("group",)
    GROUP_FIELD_NUMBER: _ClassVar[int]
    group: str
    def __init__(self, group: _Optional[str] = ...) -> None: ...

class TestDelayRequest(_message.Message):
    __slots__ = ("proxy", "test_url", "timeout_ms")
    PROXY_FIELD_NUMBER: _ClassVar[int]
    TEST_URL_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    proxy: str
    test_url: str
    timeout_ms: int
    def __init__(self, proxy: _Optional[str] = ..., test_url: _Optional[str] = ..., timeout_ms: _Optional[int] = ...) -> None: ...

class TestDelayResponse(_message.Message):
    __slots__ = ("delay_ms",)
    DELAY_MS_FIELD_NUMBER: _ClassVar[int]
    delay_ms: int
    def __init__(self, delay_ms: _Optional[int] = ...) -> None: ...

class Provider(_message.Message):
    __slots__ = ("name", "kind", "vehicle_type", "updated_at_unix_ms")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    VEHICLE_TYPE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    name: str
    kind: ProviderKind
    vehicle_type: str
    updated_at_unix_ms: int
    def __init__(self, name: _Optional[str] = ..., kind: _Optional[_Union[ProviderKind, str]] = ..., vehicle_type: _Optional[str] = ..., updated_at_unix_ms: _Optional[int] = ...) -> None: ...

class ListProvidersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListProvidersResponse(_message.Message):
    __slots__ = ("providers",)
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    providers: _containers.RepeatedCompositeFieldContainer[Provider]
    def __init__(self, providers: _Optional[_Iterable[_Union[Provider, _Mapping]]] = ...) -> None: ...

class UpdateProviderRequest(_message.Message):
    __slots__ = ("kind", "name")
    KIND_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    kind: ProviderKind
    name: str
    def __init__(self, kind: _Optional[_Union[ProviderKind, str]] = ..., name: _Optional[str] = ...) -> None: ...

class Rule(_message.Message):
    __slots__ = ("type", "payload", "proxy", "entry_id")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    PROXY_FIELD_NUMBER: _ClassVar[int]
    ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    type: str
    payload: str
    proxy: str
    entry_id: str
    def __init__(self, type: _Optional[str] = ..., payload: _Optional[str] = ..., proxy: _Optional[str] = ..., entry_id: _Optional[str] = ...) -> None: ...

class ListRulesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListRulesResponse(_message.Message):
    __slots__ = ("rules",)
    RULES_FIELD_NUMBER: _ClassVar[int]
    rules: _containers.RepeatedCompositeFieldContainer[Rule]
    def __init__(self, rules: _Optional[_Iterable[_Union[Rule, _Mapping]]] = ...) -> None: ...

class CloseConnectionRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class CloseAllConnectionsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class FlushFakeIPRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class FlushDNSCacheRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SubscribeLogsRequest(_message.Message):
    __slots__ = ("level",)
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    level: LogLevel
    def __init__(self, level: _Optional[_Union[LogLevel, str]] = ...) -> None: ...

class SubscribeTrafficRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SubscribeConnectionsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SubscribeResponse(_message.Message):
    __slots__ = ("subscription_id",)
    SUBSCRIPTION_ID_FIELD_NUMBER: _ClassVar[int]
    subscription_id: int
    def __init__(self, subscription_id: _Optional[int] = ...) -> None: ...

class PollEventsRequest(_message.Message):
    __slots__ = ("subscription_id", "max_events")
    SUBSCRIPTION_ID_FIELD_NUMBER: _ClassVar[int]
    MAX_EVENTS_FIELD_NUMBER: _ClassVar[int]
    subscription_id: int
    max_events: int
    def __init__(self, subscription_id: _Optional[int] = ..., max_events: _Optional[int] = ...) -> None: ...

class PollEventsResponse(_message.Message):
    __slots__ = ("events", "dropped")
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    DROPPED_FIELD_NUMBER: _ClassVar[int]
    events: _containers.RepeatedCompositeFieldContainer[Event]
    dropped: int
    def __init__(self, events: _Optional[_Iterable[_Union[Event, _Mapping]]] = ..., dropped: _Optional[int] = ...) -> None: ...

class UnsubscribeRequest(_message.Message):
    __slots__ = ("subscription_id",)
    SUBSCRIPTION_ID_FIELD_NUMBER: _ClassVar[int]
    subscription_id: int
    def __init__(self, subscription_id: _Optional[int] = ...) -> None: ...

class Event(_message.Message):
    __slots__ = ("log", "traffic", "connections")
    LOG_FIELD_NUMBER: _ClassVar[int]
    TRAFFIC_FIELD_NUMBER: _ClassVar[int]
    CONNECTIONS_FIELD_NUMBER: _ClassVar[int]
    log: LogEntry
    traffic: TrafficSample
    connections: ConnectionSnapshot
    def __init__(self, log: _Optional[_Union[LogEntry, _Mapping]] = ..., traffic: _Optional[_Union[TrafficSample, _Mapping]] = ..., connections: _Optional[_Union[ConnectionSnapshot, _Mapping]] = ...) -> None: ...

class LogEntry(_message.Message):
    __slots__ = ("level", "message", "time_unix_ms")
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    TIME_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    level: LogLevel
    message: str
    time_unix_ms: int
    def __init__(self, level: _Optional[_Union[LogLevel, str]] = ..., message: _Optional[str] = ..., time_unix_ms: _Optional[int] = ...) -> None: ...

class TrafficSample(_message.Message):
    __slots__ = ("up_bytes_per_second", "down_bytes_per_second", "up_total_bytes", "down_total_bytes")
    UP_BYTES_PER_SECOND_FIELD_NUMBER: _ClassVar[int]
    DOWN_BYTES_PER_SECOND_FIELD_NUMBER: _ClassVar[int]
    UP_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    DOWN_TOTAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    up_bytes_per_second: int
    down_bytes_per_second: int
    up_total_bytes: int
    down_total_bytes: int
    def __init__(self, up_bytes_per_second: _Optional[int] = ..., down_bytes_per_second: _Optional[int] = ..., up_total_bytes: _Optional[int] = ..., down_total_bytes: _Optional[int] = ...) -> None: ...

class Connection(_message.Message):
    __slots__ = ("id", "network", "source", "destination", "host", "process", "chains", "rule", "rule_payload", "upload_bytes", "download_bytes", "started_at_unix_ms", "rule_entry_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    NETWORK_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PROCESS_FIELD_NUMBER: _ClassVar[int]
    CHAINS_FIELD_NUMBER: _ClassVar[int]
    RULE_FIELD_NUMBER: _ClassVar[int]
    RULE_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_BYTES_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_BYTES_FIELD_NUMBER: _ClassVar[int]
    STARTED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    RULE_ENTRY_ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    network: str
    source: str
    destination: str
    host: str
    process: str
    chains: _containers.RepeatedScalarFieldContainer[str]
    rule: str
    rule_payload: str
    upload_bytes: int
    download_bytes: int
    started_at_unix_ms: int
    rule_entry_id: str
    def __init__(self, id: _Optional[str] = ..., network: _Optional[str] = ..., source: _Optional[str] = ..., destination: _Optional[str] = ..., host: _Optional[str] = ..., process: _Optional[str] = ..., chains: _Optional[_Iterable[str]] = ..., rule: _Optional[str] = ..., rule_payload: _Optional[str] = ..., upload_bytes: _Optional[int] = ..., download_bytes: _Optional[int] = ..., started_at_unix_ms: _Optional[int] = ..., rule_entry_id: _Optional[str] = ...) -> None: ...

class ConnectionSnapshot(_message.Message):
    __slots__ = ("connections",)
    CONNECTIONS_FIELD_NUMBER: _ClassVar[int]
    connections: _containers.RepeatedCompositeFieldContainer[Connection]
    def __init__(self, connections: _Optional[_Iterable[_Union[Connection, _Mapping]]] = ...) -> None: ...

class InspectSourceRequest(_message.Message):
    __slots__ = ("yaml", "info_filter")
    YAML_FIELD_NUMBER: _ClassVar[int]
    INFO_FILTER_FIELD_NUMBER: _ClassVar[int]
    yaml: str
    info_filter: str
    def __init__(self, yaml: _Optional[str] = ..., info_filter: _Optional[str] = ...) -> None: ...

class InspectSourceResponse(_message.Message):
    __slots__ = ("nodes", "has_rules", "group_count", "rule_count")
    NODES_FIELD_NUMBER: _ClassVar[int]
    HAS_RULES_FIELD_NUMBER: _ClassVar[int]
    GROUP_COUNT_FIELD_NUMBER: _ClassVar[int]
    RULE_COUNT_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[SourceNode]
    has_rules: bool
    group_count: int
    rule_count: int
    def __init__(self, nodes: _Optional[_Iterable[_Union[SourceNode, _Mapping]]] = ..., has_rules: _Optional[bool] = ..., group_count: _Optional[int] = ..., rule_count: _Optional[int] = ...) -> None: ...

class SourceNode(_message.Message):
    __slots__ = ("name", "type", "region", "info", "mesh")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    INFO_FIELD_NUMBER: _ClassVar[int]
    MESH_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    region: str
    info: bool
    mesh: bool
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., region: _Optional[str] = ..., info: _Optional[bool] = ..., mesh: _Optional[bool] = ...) -> None: ...

class RegisterWarpRequest(_message.Message):
    __slots__ = ("via", "protocol", "device_id", "token")
    VIA_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    via: str
    protocol: WarpProtocol
    device_id: str
    token: str
    def __init__(self, via: _Optional[str] = ..., protocol: _Optional[_Union[WarpProtocol, str]] = ..., device_id: _Optional[str] = ..., token: _Optional[str] = ...) -> None: ...

class RegisterWarpResponse(_message.Message):
    __slots__ = ("account",)
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    account: WarpAccount
    def __init__(self, account: _Optional[_Union[WarpAccount, _Mapping]] = ...) -> None: ...

class ListGeoCategoriesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListGeoCategoriesResponse(_message.Message):
    __slots__ = ("geosite", "geoip")
    GEOSITE_FIELD_NUMBER: _ClassVar[int]
    GEOIP_FIELD_NUMBER: _ClassVar[int]
    geosite: GeoCategories
    geoip: GeoCategories
    def __init__(self, geosite: _Optional[_Union[GeoCategories, _Mapping]] = ..., geoip: _Optional[_Union[GeoCategories, _Mapping]] = ...) -> None: ...

class GeoCategories(_message.Message):
    __slots__ = ("names", "sha256", "updated_at_unix_ms")
    NAMES_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    names: _containers.RepeatedScalarFieldContainer[str]
    sha256: str
    updated_at_unix_ms: int
    def __init__(self, names: _Optional[_Iterable[str]] = ..., sha256: _Optional[str] = ..., updated_at_unix_ms: _Optional[int] = ...) -> None: ...

class PreviewGroupRequest(_message.Message):
    __slots__ = ("composition", "group_id")
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    GROUP_ID_FIELD_NUMBER: _ClassVar[int]
    composition: Composition
    group_id: str
    def __init__(self, composition: _Optional[_Union[Composition, _Mapping]] = ..., group_id: _Optional[str] = ...) -> None: ...

class PreviewGroupResponse(_message.Message):
    __slots__ = ("nodes", "others")
    NODES_FIELD_NUMBER: _ClassVar[int]
    OTHERS_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[PoolNode]
    others: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, nodes: _Optional[_Iterable[_Union[PoolNode, _Mapping]]] = ..., others: _Optional[_Iterable[str]] = ...) -> None: ...

class MatchRuleRequest(_message.Message):
    __slots__ = ("config_yaml", "composition", "overrides", "host", "port", "network")
    CONFIG_YAML_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    NETWORK_FIELD_NUMBER: _ClassVar[int]
    config_yaml: str
    composition: Composition
    overrides: _containers.RepeatedScalarFieldContainer[str]
    host: str
    port: int
    network: str
    def __init__(self, config_yaml: _Optional[str] = ..., composition: _Optional[_Union[Composition, _Mapping]] = ..., overrides: _Optional[_Iterable[str]] = ..., host: _Optional[str] = ..., port: _Optional[int] = ..., network: _Optional[str] = ...) -> None: ...

class MatchRuleResponse(_message.Message):
    __slots__ = ("matched", "index", "rule", "chain", "resolved_ip")
    MATCHED_FIELD_NUMBER: _ClassVar[int]
    INDEX_FIELD_NUMBER: _ClassVar[int]
    RULE_FIELD_NUMBER: _ClassVar[int]
    CHAIN_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_IP_FIELD_NUMBER: _ClassVar[int]
    matched: bool
    index: int
    rule: Rule
    chain: _containers.RepeatedScalarFieldContainer[str]
    resolved_ip: str
    def __init__(self, matched: _Optional[bool] = ..., index: _Optional[int] = ..., rule: _Optional[_Union[Rule, _Mapping]] = ..., chain: _Optional[_Iterable[str]] = ..., resolved_ip: _Optional[str] = ...) -> None: ...

class ConvertToPlanRequest(_message.Message):
    __slots__ = ("yaml", "source_id", "locale", "info_filter")
    YAML_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    LOCALE_FIELD_NUMBER: _ClassVar[int]
    INFO_FILTER_FIELD_NUMBER: _ClassVar[int]
    yaml: str
    source_id: str
    locale: str
    info_filter: str
    def __init__(self, yaml: _Optional[str] = ..., source_id: _Optional[str] = ..., locale: _Optional[str] = ..., info_filter: _Optional[str] = ...) -> None: ...

class ConvertToPlanResponse(_message.Message):
    __slots__ = ("plan_json", "group_count", "merged_group_count", "rule_count", "raw_rule_count", "entry_count", "skipped")
    PLAN_JSON_FIELD_NUMBER: _ClassVar[int]
    GROUP_COUNT_FIELD_NUMBER: _ClassVar[int]
    MERGED_GROUP_COUNT_FIELD_NUMBER: _ClassVar[int]
    RULE_COUNT_FIELD_NUMBER: _ClassVar[int]
    RAW_RULE_COUNT_FIELD_NUMBER: _ClassVar[int]
    ENTRY_COUNT_FIELD_NUMBER: _ClassVar[int]
    SKIPPED_FIELD_NUMBER: _ClassVar[int]
    plan_json: str
    group_count: int
    merged_group_count: int
    rule_count: int
    raw_rule_count: int
    entry_count: int
    skipped: _containers.RepeatedCompositeFieldContainer[SkippedRule]
    def __init__(self, plan_json: _Optional[str] = ..., group_count: _Optional[int] = ..., merged_group_count: _Optional[int] = ..., rule_count: _Optional[int] = ..., raw_rule_count: _Optional[int] = ..., entry_count: _Optional[int] = ..., skipped: _Optional[_Iterable[_Union[SkippedRule, _Mapping]]] = ...) -> None: ...

class SkippedRule(_message.Message):
    __slots__ = ("rule", "reason")
    RULE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    rule: str
    reason: str
    def __init__(self, rule: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class ExportPlanRequest(_message.Message):
    __slots__ = ("composition", "format", "overrides", "options")
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    OPTIONS_FIELD_NUMBER: _ClassVar[int]
    composition: Composition
    format: ExportFormat
    overrides: _containers.RepeatedScalarFieldContainer[str]
    options: RunOptions
    def __init__(self, composition: _Optional[_Union[Composition, _Mapping]] = ..., format: _Optional[_Union[ExportFormat, str]] = ..., overrides: _Optional[_Iterable[str]] = ..., options: _Optional[_Union[RunOptions, _Mapping]] = ...) -> None: ...

class ExportPlanResponse(_message.Message):
    __slots__ = ("yaml",)
    YAML_FIELD_NUMBER: _ClassVar[int]
    yaml: str
    def __init__(self, yaml: _Optional[str] = ...) -> None: ...
