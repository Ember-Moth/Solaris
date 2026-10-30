// Package control decodes control Requests, runs the method and encodes the Response.
// The Windows service and the macOS extension share it (docs/architecture.md §4).
package control

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/compose"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/engine"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/frame"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/product"
)

const (
	CodeInvalidRequest       = "invalid_request"
	CodeUnsupportedVersion   = "unsupported_protocol_version"
	CodeUnsupportedMethod    = "unsupported_method"
	CodeNotImplemented       = "not_implemented"
	CodeInvalidConfig        = "invalid_config"
	CodeNotRunning           = "not_running"
	CodeAlreadyRunning       = "already_running"
	CodeApplyFailed          = "apply_failed"
	CodeUnknownSubscription  = "unknown_subscription"
	CodeTooManySubscriptions = "too_many_subscriptions"
	CodeOperationFailed      = "operation_failed"
	codeInternal             = "internal_error"
)

// Engine is what the dispatcher needs from internal/engine.
type Engine interface {
	CoreVersion() string
	State() controlpb.RunState
	Validate(engine.Profile) (*controlpb.CompositionReport, error)
	Start(engine.Profile) (*controlpb.CompositionReport, error)
	Reload(engine.Profile) (*controlpb.CompositionReport, error)
	Stop() error
	Status() *controlpb.GetStatusResponse
	SetMode(controlpb.Mode) error
	ListGroups(sort controlpb.GroupSort, excludeNotSelectable bool) []*controlpb.ProxyGroup
	GetGroup(name string, sort controlpb.GroupSort) (*controlpb.ProxyGroup, error)
	SelectProxy(group, proxy string) error
	HealthCheck(ctx context.Context, group string) error
	TestDelay(ctx context.Context, proxy, url string, timeout time.Duration) (uint32, error)
	ListProviders() []*controlpb.Provider
	UpdateProvider(kind controlpb.ProviderKind, name string) error
	ListRules() []*controlpb.Rule
	CloseConnection(id string) error
	CloseAllConnections() error
	FlushFakeIP() error
	FlushDNSCache() error
	Traffic() *controlpb.TrafficSample
	Connections() *controlpb.ConnectionSnapshot
	WatchLogs(emit func(*controlpb.LogEntry)) (stop func())
	InspectSource(source []byte, infoFilter string) (*controlpb.InspectSourceResponse, error)
	ListGeoCategories() (*controlpb.ListGeoCategoriesResponse, error)
}

type Options struct {
	Engine Engine
	// HostMethods enables the Windows-service lifecycle methods. The macOS extension
	// leaves it off because NETunnelProviderManager starts and stops the tunnel.
	HostMethods bool
	// Development marks the development server (docs/architecture.md §11), which never
	// changes the machine's network: turning on the system proxy fails with
	// unsupported_method, as does the TUN through the engine's no-TUN mode.
	Development bool
}

type Dispatcher struct {
	engine      Engine
	hostMethods bool
	development bool
	// ops serializes the methods that change state (§4.1); queries run concurrently.
	ops           sync.Mutex
	subscriptions *subscriptions
}

func New(options Options) *Dispatcher {
	return &Dispatcher{
		engine:        options.Engine,
		hostMethods:   options.HostMethods,
		development:   options.Development,
		subscriptions: newSubscriptions(options.Engine),
	}
}

// Close ends every subscription. It does not stop the engine.
func (d *Dispatcher) Close() {
	d.subscriptions.close()
}

// Handle answers one encoded Request. Every failure, including an undecodable request,
// becomes an Error result, so the caller always has a Response to send back. ctx lives
// as long as the caller's connection; subscriptions made on it end with it.
func (d *Dispatcher) Handle(ctx context.Context, caller peer.Identity, payload []byte) []byte {
	if len(payload) > frame.MaxRequestBytes {
		return EncodeFailure(0, CodeInvalidRequest, "request exceeds the size limit")
	}
	var request controlpb.Request
	if err := proto.Unmarshal(payload, &request); err != nil {
		return EncodeFailure(0, CodeInvalidRequest, "malformed request")
	}
	id := request.GetId()
	switch {
	case request.GetProtocolVersion() != product.ProtocolVersion:
		return EncodeFailure(id, CodeUnsupportedVersion, fmt.Sprintf(
			"protocol version %d is not supported; this side speaks %d",
			request.GetProtocolVersion(), product.ProtocolVersion))
	case id == 0:
		return EncodeFailure(0, CodeInvalidRequest, "request id must be non-zero")
	case request.Method == nil:
		return EncodeFailure(id, CodeInvalidRequest, "request names no known method")
	}
	return encode(id, d.dispatch(ctx, caller, &request))
}

func (d *Dispatcher) dispatch(ctx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	switch method := request.Method.(type) {
	case *controlpb.Request_GetInfo:
		return &controlpb.Response{Result: &controlpb.Response_GetInfo{GetInfo: d.info()}}
	case *controlpb.Request_Claim, *controlpb.Request_Validate, *controlpb.Request_Start,
		*controlpb.Request_Stop, *controlpb.Request_SetSystemProxy:
		if !d.hostMethods {
			return failure(CodeUnsupportedMethod, methodName(request)+" is only served by the Windows service")
		}
		return d.hostMethod(request)
	case *controlpb.Request_Reload:
		return d.locked(func() *controlpb.Response { return d.reload(method.Reload) })
	default:
		return d.coreMethod(ctx, caller, request)
	}
}

func (d *Dispatcher) hostMethod(request *controlpb.Request) *controlpb.Response {
	d.ops.Lock()
	defer d.ops.Unlock()
	switch method := request.Method.(type) {
	case *controlpb.Request_Claim:
		if !d.development {
			return failure(CodeNotImplemented, "claim is not implemented yet")
		}
		// The development socket only admits the user running the server.
		return empty()
	case *controlpb.Request_Validate:
		validate := method.Validate
		profile := engine.Profile{
			Config:      []byte(validate.GetConfigYaml()),
			Composition: composition(validate.GetComposition()),
			Overrides:   overrides(validate.GetOverrides()),
		}
		return d.applied(d.engine.Validate(profile))
	case *controlpb.Request_Start:
		start := method.Start
		profile, response := d.profile(start.GetConfigYaml(), start.GetComposition(), start.GetOverrides(), start.GetOptions())
		if response != nil {
			return response
		}
		return d.applied(d.engine.Start(profile))
	case *controlpb.Request_Stop:
		return d.result(d.engine.Stop())
	case *controlpb.Request_SetSystemProxy:
		return d.systemProxy(method.SetSystemProxy.GetEnabled())
	}
	return failure(codeInternal, "unrouted host method")
}

func (d *Dispatcher) reload(reload *controlpb.ReloadRequest) *controlpb.Response {
	profile, response := d.profile(reload.GetConfigYaml(), reload.GetComposition(), reload.GetOverrides(), reload.GetOptions())
	if response != nil {
		return response
	}
	return d.applied(d.engine.Reload(profile))
}

// applied answers Validate, Start and Reload: a composition's report, or Empty.
func (d *Dispatcher) applied(report *controlpb.CompositionReport, err error) *controlpb.Response {
	if err != nil || report == nil {
		return d.result(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_Composition{Composition: report}}
}

// composition is the request's node pool and plan, or nil when it carries config_yaml.
func composition(request *controlpb.Composition) *compose.Composition {
	if request == nil {
		return nil
	}
	out := &compose.Composition{
		InfoFilter: request.GetInfoFilter(),
		Plan:       []byte(request.GetPlanJson()),
	}
	for _, source := range request.GetSources() {
		out.Sources = append(out.Sources, compose.Source{
			ID:   source.GetId(),
			Name: source.GetName(),
			YAML: []byte(source.GetYaml()),
		})
	}
	return out
}

// overrides keeps the order the UI sends: the global override, then the profile's own.
func overrides(sources []string) [][]byte {
	out := make([][]byte, len(sources))
	for i, source := range sources {
		out[i] = []byte(source)
	}
	return out
}

func (d *Dispatcher) profile(configYAML string, source *controlpb.Composition, overrideYAML []string, options *controlpb.RunOptions) (engine.Profile, *controlpb.Response) {
	if options.GetMixedPort() > 0xffff {
		return engine.Profile{}, failure(CodeInvalidRequest, "mixed_port must be a TCP port")
	}
	if options.GetSystemProxyEnabled() {
		if response := d.systemProxy(true); response.GetError() != nil {
			return engine.Profile{}, response
		}
	}
	return engine.Profile{
		Config:      []byte(configYAML),
		Composition: composition(source),
		Overrides:   overrides(overrideYAML),
		Options: engine.RunOptions{
			TUN:       options.GetTunEnabled(),
			MixedPort: uint16(options.GetMixedPort()),
			AllowLAN:  options.GetAllowLan(),
		},
	}, nil
}

// systemProxy answers a request to set the owner's system proxy. The development server
// never touches it, and the Windows implementation arrives with M4 (§5.5).
func (d *Dispatcher) systemProxy(enabled bool) *controlpb.Response {
	switch {
	case d.development && enabled:
		return failure(CodeUnsupportedMethod, "the development server never sets the system proxy")
	case d.development:
		return empty()
	default:
		return failure(CodeNotImplemented, "the system proxy is not implemented yet")
	}
}

func (d *Dispatcher) locked(run func() *controlpb.Response) *controlpb.Response {
	d.ops.Lock()
	defer d.ops.Unlock()
	return run()
}

func (d *Dispatcher) coreMethod(ctx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	switch method := request.Method.(type) {
	case *controlpb.Request_GetStatus:
		// The system proxy is never on until the Windows service sets it (M4).
		return &controlpb.Response{Result: &controlpb.Response_GetStatus{GetStatus: d.engine.Status()}}
	case *controlpb.Request_SetMode:
		return d.locked(func() *controlpb.Response { return d.result(d.engine.SetMode(method.SetMode.GetMode())) })
	case *controlpb.Request_ListGroups:
		sort := method.ListGroups.GetSort()
		if !knownSort(sort) {
			return failure(CodeInvalidRequest, "unknown group sort")
		}
		groups := d.engine.ListGroups(sort, method.ListGroups.GetExcludeNotSelectable())
		return &controlpb.Response{Result: &controlpb.Response_ListGroups{ListGroups: &controlpb.ListGroupsResponse{Groups: groups}}}
	case *controlpb.Request_GetGroup:
		get := method.GetGroup
		if get.GetName() == "" || !knownSort(get.GetSort()) {
			return failure(CodeInvalidRequest, "get_group needs a group name and a known sort")
		}
		group, err := d.engine.GetGroup(get.GetName(), get.GetSort())
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_GetGroup{GetGroup: &controlpb.GetGroupResponse{Group: group}}}
	case *controlpb.Request_SelectProxy:
		selectProxy := method.SelectProxy
		if selectProxy.GetGroup() == "" || selectProxy.GetProxy() == "" {
			return failure(CodeInvalidRequest, "select_proxy needs a group and a proxy")
		}
		return d.locked(func() *controlpb.Response {
			return d.result(d.engine.SelectProxy(selectProxy.GetGroup(), selectProxy.GetProxy()))
		})
	case *controlpb.Request_HealthCheck:
		return d.result(d.engine.HealthCheck(ctx, method.HealthCheck.GetGroup()))
	case *controlpb.Request_TestDelay:
		test := method.TestDelay
		if test.GetProxy() == "" {
			return failure(CodeInvalidRequest, "test_delay needs a proxy")
		}
		timeout := time.Duration(test.GetTimeoutMs()) * time.Millisecond
		delay, err := d.engine.TestDelay(ctx, test.GetProxy(), test.GetTestUrl(), timeout)
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_TestDelay{TestDelay: &controlpb.TestDelayResponse{DelayMs: delay}}}
	case *controlpb.Request_ListProviders:
		providers := d.engine.ListProviders()
		return &controlpb.Response{Result: &controlpb.Response_ListProviders{ListProviders: &controlpb.ListProvidersResponse{Providers: providers}}}
	case *controlpb.Request_UpdateProvider:
		update := method.UpdateProvider
		if update.GetName() == "" {
			return failure(CodeInvalidRequest, "update_provider needs a provider name")
		}
		return d.result(d.engine.UpdateProvider(update.GetKind(), update.GetName()))
	case *controlpb.Request_ListRules:
		rules := d.engine.ListRules()
		return &controlpb.Response{Result: &controlpb.Response_ListRules{ListRules: &controlpb.ListRulesResponse{Rules: rules}}}
	case *controlpb.Request_CloseConnection:
		if method.CloseConnection.GetId() == "" {
			return failure(CodeInvalidRequest, "close_connection needs a connection id")
		}
		return d.result(d.engine.CloseConnection(method.CloseConnection.GetId()))
	case *controlpb.Request_CloseAllConnections:
		return d.result(d.engine.CloseAllConnections())
	case *controlpb.Request_FlushFakeIp:
		return d.result(d.engine.FlushFakeIP())
	case *controlpb.Request_FlushDnsCache:
		return d.result(d.engine.FlushDNSCache())
	case *controlpb.Request_SubscribeLogs:
		level := method.SubscribeLogs.GetLevel()
		if level == controlpb.LogLevel_LOG_LEVEL_UNSPECIFIED {
			level = controlpb.LogLevel_LOG_LEVEL_INFO
		}
		if _, ok := controlpb.LogLevel_name[int32(level)]; !ok {
			return failure(CodeInvalidRequest, "unknown log level")
		}
		return d.subscribe(ctx, caller, topicLogs, level)
	case *controlpb.Request_SubscribeTraffic:
		return d.subscribe(ctx, caller, topicTraffic, 0)
	case *controlpb.Request_SubscribeConnections:
		return d.subscribe(ctx, caller, topicConnections, 0)
	case *controlpb.Request_PollEvents:
		poll := method.PollEvents
		events, dropped, err := d.subscriptions.poll(caller, poll.GetSubscriptionId(), poll.GetMaxEvents())
		if err != nil {
			return subscriptionFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_PollEvents{PollEvents: &controlpb.PollEventsResponse{
			Events:  events,
			Dropped: dropped,
		}}}
	case *controlpb.Request_Unsubscribe:
		if err := d.subscriptions.unsubscribe(caller, method.Unsubscribe.GetSubscriptionId()); err != nil {
			return subscriptionFailure(err)
		}
		return empty()
	case *controlpb.Request_InspectSource:
		inspect := method.InspectSource
		inspection, err := d.engine.InspectSource([]byte(inspect.GetYaml()), inspect.GetInfoFilter())
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_InspectSource{InspectSource: inspection}}
	case *controlpb.Request_ListGeoCategories:
		categories, err := d.engine.ListGeoCategories()
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_ListGeoCategories{ListGeoCategories: categories}}
	}
	return failure(CodeNotImplemented, methodName(request)+" is not implemented")
}

func (d *Dispatcher) subscribe(ctx context.Context, caller peer.Identity, kind topic, level controlpb.LogLevel) *controlpb.Response {
	id, err := d.subscriptions.subscribe(ctx, caller, kind, level)
	if err != nil {
		return subscriptionFailure(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_Subscribe{Subscribe: &controlpb.SubscribeResponse{SubscriptionId: id}}}
}

func knownSort(sort controlpb.GroupSort) bool {
	_, ok := controlpb.GroupSort_name[int32(sort)]
	return ok
}

func (d *Dispatcher) info() *controlpb.GetInfoResponse {
	ownership := controlpb.Ownership_OWNERSHIP_UNSPECIFIED
	if d.hostMethods {
		ownership = controlpb.Ownership_OWNERSHIP_AVAILABLE
	}
	return &controlpb.GetInfoResponse{
		ProtocolVersion: product.ProtocolVersion,
		ServiceVersion:  product.Version,
		CoreVersion:     d.engine.CoreVersion(),
		State:           d.engine.State(),
		Ownership:       ownership,
	}
}

func (d *Dispatcher) result(err error) *controlpb.Response {
	if err != nil {
		return engineFailure(err)
	}
	return empty()
}

// engineFailure maps engine errors to protocol codes. The message drops the sentinel's
// own text so the UI can show mihomo's error as it is.
func engineFailure(err error) *controlpb.Response {
	for _, known := range []struct {
		sentinel  error
		code      string
		retryable bool
	}{
		{engine.ErrInvalidConfig, CodeInvalidConfig, false},
		{engine.ErrNotRunning, CodeNotRunning, false},
		{engine.ErrAlreadyRunning, CodeAlreadyRunning, false},
		{engine.ErrUnsupported, CodeUnsupportedMethod, false},
		{engine.ErrInvalidArgument, CodeInvalidRequest, false},
		{engine.ErrApplyFailed, CodeApplyFailed, true},
	} {
		if errors.Is(err, known.sentinel) {
			response := failure(known.code, strings.TrimPrefix(err.Error(), known.sentinel.Error()+": "))
			response.GetError().Retryable = known.retryable
			return response
		}
	}
	response := failure(CodeOperationFailed, err.Error())
	response.GetError().Retryable = true
	return response
}

func subscriptionFailure(err error) *controlpb.Response {
	if errors.Is(err, errTooManySubscriptions) {
		response := failure(CodeTooManySubscriptions, err.Error())
		response.GetError().Retryable = true
		return response
	}
	return failure(CodeUnknownSubscription, err.Error())
}

func methodName(request *controlpb.Request) string {
	message := request.ProtoReflect()
	field := message.WhichOneof(message.Descriptor().Oneofs().ByName("method"))
	if field == nil {
		return "request"
	}
	return string(field.Name())
}

func empty() *controlpb.Response {
	return &controlpb.Response{Result: &controlpb.Response_Empty{Empty: &controlpb.Empty{}}}
}

func failure(code string, message string) *controlpb.Response {
	return &controlpb.Response{Result: &controlpb.Response_Error{Error: &controlpb.Error{
		Code:    code,
		Message: strings.ToValidUTF8(message, "�"),
	}}}
}

// EncodeFailure builds an encoded Error response for hosts that reject a request
// before it reaches Handle.
func EncodeFailure(id uint64, code string, message string) []byte {
	return encode(id, failure(code, message))
}

func encode(id uint64, response *controlpb.Response) []byte {
	response.ProtocolVersion = product.ProtocolVersion
	response.Id = id
	payload, err := proto.Marshal(response)
	if err != nil {
		payload, _ = proto.Marshal(&controlpb.Response{
			ProtocolVersion: product.ProtocolVersion,
			Id:              id,
			Result:          &controlpb.Response_Error{Error: &controlpb.Error{Code: codeInternal}},
		})
	}
	return payload
}
