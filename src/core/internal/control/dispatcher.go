// Package control decodes control Requests, runs the method and encodes the Response.
// The Windows service and the macOS extension share it (docs/architecture.md §4).
package control

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
	"github.com/Ember-Moth/Solaris/src/core/internal/warp"
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
	CodeNotClaimed           = "not_claimed"
	CodeOwnedByOtherUser     = "owned_by_other_user"
	CodeServiceRestarting    = "service_restarting"
	codeInternal             = "internal_error"
)

// The Windows service's answers to callers that are not its owner (docs/architecture.md §5.4).
var (
	// ErrNotClaimed: the caller is not the owner, and Claim would make them so.
	ErrNotClaimed = errors.New("the service is not yours; claim it first")
	// ErrOwnedByOtherUser: another user owns the service and their tunnel is up.
	ErrOwnedByOtherUser = errors.New("another user's tunnel is running")
	// ErrRestarting: a new service process takes on the new owner, since mihomo's home is
	// fixed for the life of a process; claim again once it is back.
	ErrRestarting = errors.New("the service restarts for its new owner; claim again")
)

// Engine is what the dispatcher needs from internal/engine.
type Engine interface {
	CoreVersion() string
	State() controlpb.RunState
	Validate(admission.Profile) (*controlpb.CompositionReport, error)
	Start(admission.Profile) (*controlpb.CompositionReport, error)
	Reload(admission.Profile) (*controlpb.CompositionReport, error)
	Stop() error
	Status() *controlpb.GetStatusResponse
	SetMode(controlpb.Mode) error
	ListGroups(sort controlpb.GroupSort, excludeNotSelectable bool) []*controlpb.ProxyGroup
	GetGroup(name string, sort controlpb.GroupSort) (*controlpb.ProxyGroup, error)
	SelectProxy(group, proxy string) error
	HealthCheck(ctx context.Context, group string, test controlpb.DelayTest) error
	TestDelay(ctx context.Context, proxy, url string, timeout time.Duration, test controlpb.DelayTest) (uint32, error)
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
	PreviewGroup(composition compose.Composition, groupID string) (*controlpb.PreviewGroupResponse, error)
	MatchRule(engine.MatchRequest) (*controlpb.MatchRuleResponse, error)
	ConvertToPlan(source []byte, options compose.Conversion) (*controlpb.ConvertToPlanResponse, error)
	ExportPlan(profile admission.Profile, format controlpb.ExportFormat) ([]byte, error)
	RegisterWarp(ctx context.Context, via string, request warp.Request) (*controlpb.WarpAccount, error)
	ListVpnGate(ctx context.Context, via string) ([]*controlpb.VpnGateServer, error)
	MeshStatus(ctx context.Context) ([]*controlpb.MeshStatus, error)
	LoginMesh(ctx context.Context, name string) (string, error)
}

// Host is the Windows service's part of the host methods (docs/architecture.md §5): who may
// use the core, the profile that comes back when the owner signs in again, and the owner's
// system proxy. The macOS extension and the development server have none.
type Host interface {
	// Ownership is how caller stands to the owner, for GetInfo.
	Ownership(caller peer.Identity) controlpb.Ownership
	// Admit lets the owner through; anyone else gets ErrNotClaimed or ErrOwnedByOtherUser.
	Admit(caller peer.Identity) error
	// Claim makes caller the owner when the service has none, is theirs already, or its
	// owner's tunnel is down. ErrRestarting means a new process will serve them.
	Claim(caller peer.Identity) error
	// TakeOver is Claim that lets an administrator take over another user's running tunnel
	// too (decision D5b): stop brings it down first. Anyone else gets what Claim gives them.
	TakeOver(caller peer.Identity, stop func() error) error
	// Applied follows Start, Reload and Resume once the core took start: the service keeps
	// it for the next sign-in and sets the owner's system proxy as its options say.
	Applied(start *controlpb.StartRequest) error
	// StopRequested authorizes caller again and keeps the stop intent before waiting
	// for a busy core. ResumeAllowed rechecks it when an automatic resume executes.
	StopRequested(caller peer.Identity) error
	ResumeAllowed() bool
	RecoveryStatus() (controlpb.RecoveryState, string)
	// Stopped follows Stop, after which the tunnel stays down; Suspended follows Suspend,
	// after which it comes back at the owner's next sign-in. Both restore their proxy.
	Stopped() error
	Suspending(controlpb.Mode)
	Suspended() error
	SetSystemProxy(enabled bool) error
	SystemProxyEnabled() bool
}

type Options struct {
	// Engine is nil in a Windows service that has no owner yet: it answers GetInfo and Claim
	// only, since mihomo's home is the owner's (§3.2).
	Engine Engine
	// HostMethods enables Claim and SetSystemProxy, which only the Windows service (and
	// the development server) has. Validate, Start, Reload and Stop run on every host: the
	// macOS extension gets its configuration the same way (D7), while
	// NETunnelProviderManager starts and stops the tunnel around it.
	HostMethods bool
	// Development marks the development server (docs/architecture.md §11), which never
	// changes the machine's network: turning on the system proxy fails with
	// unsupported_method, as does the TUN through the engine's no-TUN mode.
	Development bool
	// Host is the Windows service's owner, persistence and system proxy.
	Host Host
	// OnUnresponsive runs once when work outlives its core deadline. The host must replace
	// the process: mihomo's package state cannot safely be reused after a stuck mutation.
	OnUnresponsive func(operation.Failure)
}

type Dispatcher struct {
	engine         Engine
	hostMethods    bool
	development    bool
	host           Host
	operations     *operation.Executor
	reads          *operation.Executor
	intents        *operation.Executor
	intentMu       sync.Mutex
	stopGeneration atomic.Uint64
	lifetime       context.Context
	closeLifetime  context.CancelFunc
	unresponsive   atomic.Bool
	failOnce       sync.Once
	onUnresponsive func(operation.Failure)
	subscriptions  *subscriptions
}

func New(options Options) *Dispatcher {
	dispatcher := &Dispatcher{
		engine:         options.Engine,
		hostMethods:    options.HostMethods,
		development:    options.Development,
		host:           options.Host,
		onUnresponsive: options.OnUnresponsive,
		subscriptions:  newSubscriptions(options.Engine),
	}
	dispatcher.operations = operation.New(1, dispatcher.fail)
	dispatcher.reads = operation.New(32, dispatcher.fail)
	dispatcher.intents = operation.New(1, dispatcher.fail)
	dispatcher.lifetime, dispatcher.closeLifetime = context.WithCancel(context.Background())
	return dispatcher
}

// Close ends every subscription. It does not stop the engine.
func (d *Dispatcher) Close() {
	d.closeLifetime()
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
	return encode(id, d.execute(ctx, caller, &request))
}

func (d *Dispatcher) dispatch(ctx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	switch request.Method.(type) {
	case *controlpb.Request_GetInfo:
		return &controlpb.Response{Result: &controlpb.Response_GetInfo{GetInfo: d.info(caller)}}
	case *controlpb.Request_Claim:
		if !d.hostMethods {
			return failure(CodeUnsupportedMethod, "claim is only served by the Windows service")
		}
		takeOver := request.GetClaim().GetTakeOver()
		return d.claim(ctx, caller, takeOver)
	}
	// Only the owner reaches the core (§5.4).
	if d.host != nil {
		if err := d.host.Admit(caller); err != nil {
			return hostFailure(err)
		}
	}
	if err := d.operationActive(ctx); err != nil {
		return engineFailure(err)
	}
	if d.engine == nil {
		return failure(CodeNotClaimed, ErrNotClaimed.Error())
	}
	switch method := request.Method.(type) {
	case *controlpb.Request_SetSystemProxy:
		if !d.hostMethods {
			return failure(CodeUnsupportedMethod, "set_system_proxy is only served by the Windows service")
		}
		return d.hostMethod(ctx, request)
	case *controlpb.Request_Validate, *controlpb.Request_Start, *controlpb.Request_Stop:
		return d.hostMethod(ctx, request)
	case *controlpb.Request_Reload:
		return d.reload(ctx, method.Reload)
	default:
		return d.coreMethod(ctx, caller, request)
	}
}

func (d *Dispatcher) hostMethod(ctx context.Context, request *controlpb.Request) *controlpb.Response {
	switch method := request.Method.(type) {
	case *controlpb.Request_Validate:
		validate := method.Validate
		profile := admission.Profile{
			Config:      []byte(validate.GetConfigYaml()),
			Composition: composition(validate.GetComposition()),
			Overrides:   overrides(validate.GetOverrides()),
		}
		return d.applied(d.engine.Validate(profile))
	case *controlpb.Request_Start:
		if !d.currentIntent(ctx) {
			return engineFailure(context.Canceled)
		}
		start := method.Start
		profile, response := d.profile(start.GetConfigYaml(), start.GetComposition(), start.GetOverrides(), start.GetOptions())
		if response != nil {
			return response
		}
		report, err := d.engine.Start(profile)
		if err := d.operationActive(ctx); err != nil {
			return engineFailure(err)
		}
		if err == nil {
			err = d.commitApplied(ctx, start)
		}
		return d.applied(report, err)
	case *controlpb.Request_Stop:
		err := d.engine.Stop()
		if err := d.operationActive(ctx); err != nil {
			return engineFailure(err)
		}
		if err == nil && d.host != nil {
			err = d.host.Stopped()
		}
		if intent, ok := ctx.Value(intentKey{}).(requestIntent); ok {
			err = errors.Join(err, intent.stopError)
		}
		return d.result(err)
	case *controlpb.Request_SetSystemProxy:
		return d.systemProxy(method.SetSystemProxy.GetEnabled())
	}
	return failure(codeInternal, "unrouted host method")
}

// claim answers Claim. The development socket only admits the user running the server, who
// owns it already. A take-over stops the core as Stop does, under the same lock.
func (d *Dispatcher) claim(ctx context.Context, caller peer.Identity, takeOver bool) *controlpb.Response {
	if err := d.operationActive(ctx); err != nil {
		return engineFailure(err)
	}
	switch {
	case d.development:
		return empty()
	case d.host == nil:
		return failure(CodeNotImplemented, "claim is not implemented by this host")
	}
	var err error
	if takeOver {
		err = d.host.TakeOver(caller, func() error {
			if err := d.engine.Stop(); err != nil {
				return err
			}
			return d.operationActive(ctx)
		})
	} else {
		err = d.host.Claim(caller)
	}
	if err != nil {
		return hostFailure(err)
	}
	return empty()
}

func (d *Dispatcher) reload(ctx context.Context, reload *controlpb.ReloadRequest) *controlpb.Response {
	if !d.currentIntent(ctx) {
		return engineFailure(context.Canceled)
	}
	profile, response := d.profile(reload.GetConfigYaml(), reload.GetComposition(), reload.GetOverrides(), reload.GetOptions())
	if response != nil {
		return response
	}
	report, err := d.engine.Reload(profile)
	if err := d.operationActive(ctx); err != nil {
		return engineFailure(err)
	}
	if err == nil {
		err = d.commitApplied(ctx, asStart(reload))
	}
	return d.applied(report, err)
}

// asStart is a Reload's profile as Start would carry it, which is how the service keeps it.
func asStart(reload *controlpb.ReloadRequest) *controlpb.StartRequest {
	start := &controlpb.StartRequest{Overrides: reload.GetOverrides(), Options: reload.GetOptions()}
	switch source := reload.GetSource().(type) {
	case *controlpb.ReloadRequest_ConfigYaml:
		start.Source = &controlpb.StartRequest_ConfigYaml{ConfigYaml: source.ConfigYaml}
	case *controlpb.ReloadRequest_Composition:
		start.Source = &controlpb.StartRequest_Composition{Composition: source.Composition}
	}
	return start
}

// Running reports whether the core is up or on its way up or down; a stopped or failed core
// leaves the tunnel down.
func (d *Dispatcher) Running() bool {
	if d.engine == nil {
		return false
	}
	switch d.engine.State() {
	case controlpb.RunState_RUN_STATE_STOPPED, controlpb.RunState_RUN_STATE_FAILED:
		return false
	}
	return true
}

// Resume starts the core with the profile the Windows service kept, as Start does, when its
// owner signs in again (docs/architecture.md §5.2). A core that runs is left alone.
func (d *Dispatcher) Resume(start *controlpb.StartRequest) error {
	return d.ResumeContext(context.Background(), start)
}

func (d *Dispatcher) ResumeContext(ctx context.Context, start *controlpb.StartRequest) error {
	return d.runLifecycle(ctx, "start", func(work context.Context) error { return d.resume(work, start) })
}

func (d *Dispatcher) resume(ctx context.Context, start *controlpb.StartRequest) error {
	if !d.currentIntent(ctx) || d.host == nil || !d.host.ResumeAllowed() {
		return nil
	}
	if d.Running() {
		return nil
	}
	profile, response := d.profile(start.GetConfigYaml(), start.GetComposition(), start.GetOverrides(), start.GetOptions())
	if response != nil {
		return errors.New(response.GetError().GetMessage())
	}
	if _, err := d.engine.Start(profile); err != nil {
		return err
	}
	if err := d.operationActive(ctx); err != nil {
		return err
	}
	return d.commitApplied(ctx, start)
}

// Suspend stops the core when its owner signs out (decision D5). Unlike Stop, the tunnel
// comes back at their next sign-in.
func (d *Dispatcher) Suspend() error {
	return d.SuspendContext(context.Background())
}

func (d *Dispatcher) SuspendContext(ctx context.Context) error {
	return d.runLifecycle(ctx, "stop", func(work context.Context) error {
		if d.Running() && d.host != nil {
			d.host.Suspending(d.engine.Status().GetMode())
		}
		if err := d.operationActive(work); err != nil {
			return err
		}
		if err := d.engine.Stop(); err != nil {
			return err
		}
		if err := d.operationActive(work); err != nil {
			return err
		}
		return d.host.Suspended()
	})
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
		Warp:       request.GetEgress().GetWarp(),
		VpnGate:    request.GetEgress().GetVpnGate(),
		Mesh:       request.GetMesh(),
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

func (d *Dispatcher) profile(configYAML string, source *controlpb.Composition, overrideYAML []string, options *controlpb.RunOptions) (admission.Profile, *controlpb.Response) {
	run, response := runOptions(options)
	if response != nil {
		return admission.Profile{}, response
	}
	if options.GetSystemProxyEnabled() {
		if response := d.systemProxyServed(); response != nil {
			return admission.Profile{}, response
		}
	}
	return admission.Profile{
		Config:      []byte(configYAML),
		Composition: composition(source),
		Overrides:   overrides(overrideYAML),
		Options:     run,
	}, nil
}

// tunStacks are mihomo's names for the stacks RunOptions may ask for.
var tunStacks = map[controlpb.TunStack]string{
	controlpb.TunStack_TUN_STACK_UNSPECIFIED: "",
	controlpb.TunStack_TUN_STACK_SYSTEM:      "system",
	controlpb.TunStack_TUN_STACK_MIPS:        "mips",
	controlpb.TunStack_TUN_STACK_GVISOR:      "gvisor",
	controlpb.TunStack_TUN_STACK_MIXED:       "mixed",
}

func runOptions(options *controlpb.RunOptions) (admission.RunOptions, *controlpb.Response) {
	if options.GetMixedPort() > 0xffff {
		return admission.RunOptions{}, failure(CodeInvalidRequest, "mixed_port must be a TCP port")
	}
	stack, ok := tunStacks[options.GetTunStack()]
	if !ok {
		return admission.RunOptions{}, failure(CodeInvalidRequest, "unknown TUN stack")
	}
	return admission.RunOptions{
		TUN:       options.GetTunEnabled(),
		MixedPort: uint16(options.GetMixedPort()),
		AllowLAN:  options.GetAllowLan(),
		Stack:     stack,
	}, nil
}

// systemProxy answers a request to set the owner's system proxy (§5.5). The macOS extension
// has none, and the development server never touches it.
func (d *Dispatcher) systemProxy(enabled bool) *controlpb.Response {
	if !enabled && d.development {
		return empty()
	}
	if response := d.systemProxyServed(); response != nil {
		return response
	}
	return d.result(d.host.SetSystemProxy(enabled))
}

// systemProxyServed is nil when this host can turn the system proxy on, and the refusal
// otherwise.
func (d *Dispatcher) systemProxyServed() *controlpb.Response {
	switch {
	case !d.hostMethods:
		return failure(CodeUnsupportedMethod, "only the Windows service sets the system proxy")
	case d.development:
		return failure(CodeUnsupportedMethod, "the development server never sets the system proxy")
	case d.host == nil:
		return failure(CodeNotImplemented, "the system proxy is not implemented by this host")
	}
	return nil
}

func (d *Dispatcher) coreMethod(ctx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	switch method := request.Method.(type) {
	case *controlpb.Request_GetStatus:
		status := d.engine.Status()
		if d.host != nil {
			status.SystemProxyEnabled = d.host.SystemProxyEnabled()
			status.RecoveryState, status.RecoveryError = d.host.RecoveryStatus()
		}
		return &controlpb.Response{Result: &controlpb.Response_GetStatus{GetStatus: status}}
	case *controlpb.Request_SetMode:
		return d.result(d.engine.SetMode(method.SetMode.GetMode()))
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
		return d.result(d.engine.SelectProxy(selectProxy.GetGroup(), selectProxy.GetProxy()))
	case *controlpb.Request_HealthCheck:
		return d.result(d.engine.HealthCheck(ctx, method.HealthCheck.GetGroup(), method.HealthCheck.GetTest()))
	case *controlpb.Request_TestDelay:
		test := method.TestDelay
		if test.GetProxy() == "" {
			return failure(CodeInvalidRequest, "test_delay needs a proxy")
		}
		timeout := time.Duration(test.GetTimeoutMs()) * time.Millisecond
		delay, err := d.engine.TestDelay(ctx, test.GetProxy(), test.GetTestUrl(), timeout, test.GetTest())
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
	case *controlpb.Request_PreviewGroup:
		preview := method.PreviewGroup
		if preview.GetComposition() == nil || preview.GetGroupId() == "" {
			return failure(CodeInvalidRequest, "preview_group needs a composition and a group id")
		}
		members, err := d.engine.PreviewGroup(*composition(preview.GetComposition()), preview.GetGroupId())
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_PreviewGroup{PreviewGroup: members}}
	case *controlpb.Request_MatchRule:
		return d.matchRule(method.MatchRule)
	case *controlpb.Request_ConvertToPlan:
		convert := method.ConvertToPlan
		converted, err := d.engine.ConvertToPlan([]byte(convert.GetYaml()), compose.Conversion{
			SourceID:   convert.GetSourceId(),
			Locale:     convert.GetLocale(),
			InfoFilter: convert.GetInfoFilter(),
		})
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_ConvertToPlan{ConvertToPlan: converted}}
	case *controlpb.Request_ExportPlan:
		export := method.ExportPlan
		if export.GetComposition() == nil {
			return failure(CodeInvalidRequest, "export_plan needs a composition")
		}
		if _, ok := controlpb.ExportFormat_name[int32(export.GetFormat())]; !ok {
			return failure(CodeInvalidRequest, "unknown export format")
		}
		profile, response := d.draftProfile("", export.GetComposition(), export.GetOverrides(), export.GetOptions())
		if response != nil {
			return response
		}
		exported, err := d.engine.ExportPlan(profile, export.GetFormat())
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_ExportPlan{ExportPlan: &controlpb.ExportPlanResponse{Yaml: string(exported)}}}
	case *controlpb.Request_RegisterWarp:
		return d.registerWarp(ctx, method.RegisterWarp)
	case *controlpb.Request_ListVpnGate:
		// Outside the operation lock too, as registerWarp: the list takes a while to arrive.
		if method.ListVpnGate.GetVia() == "" {
			return failure(CodeInvalidRequest, "list_vpngate needs the via group")
		}
		servers, err := d.engine.ListVpnGate(ctx, method.ListVpnGate.GetVia())
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_ListVpnGate{ListVpnGate: &controlpb.ListVpnGateResponse{Servers: servers}}}
	case *controlpb.Request_GetMeshStatus:
		statuses, err := d.engine.MeshStatus(ctx)
		if err != nil {
			return engineFailure(err)
		}
		return &controlpb.Response{Result: &controlpb.Response_GetMeshStatus{GetMeshStatus: &controlpb.GetMeshStatusResponse{Networks: statuses}}}
	case *controlpb.Request_LoginMesh:
		return d.loginMesh(ctx, method.LoginMesh)
	}
	return failure(CodeNotImplemented, methodName(request)+" is not implemented")
}

// registerWarp answers RegisterWarp (docs/exits.md §3.2). It runs outside the operation
// lock: it changes nothing the core runs, and Cloudflare may take seconds to answer.
// Cloudflare's refusals come back as operation_failed with its text.
func (d *Dispatcher) registerWarp(ctx context.Context, register *controlpb.RegisterWarpRequest) *controlpb.Response {
	switch _, known := controlpb.WarpProtocol_name[int32(register.GetProtocol())]; {
	case register.GetVia() == "":
		return failure(CodeInvalidRequest, "register_warp needs the via group")
	case !known:
		return failure(CodeInvalidRequest, "unknown WARP protocol")
	case (register.GetDeviceId() == "") != (register.GetToken() == ""):
		return failure(CodeInvalidRequest, "an existing WARP device needs both its id and its token")
	}
	account, err := d.engine.RegisterWarp(ctx, register.GetVia(), warp.Request{
		Protocol: register.GetProtocol(),
		DeviceID: register.GetDeviceId(),
		Token:    register.GetToken(),
	})
	if err != nil {
		return engineFailure(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_RegisterWarp{RegisterWarp: &controlpb.RegisterWarpResponse{Account: account}}}
}

// loginMesh answers LoginMesh (docs/mesh.md §10.5). Like registerWarp it runs outside the
// operation lock: Tailscale may take seconds to hand out a URL. A URL that did not come in
// time is worth asking for again.
func (d *Dispatcher) loginMesh(ctx context.Context, login *controlpb.LoginMeshRequest) *controlpb.Response {
	if login.GetName() == "" {
		return failure(CodeInvalidRequest, "login_mesh needs the outbound's name")
	}
	url, err := d.engine.LoginMesh(ctx, login.GetName())
	switch {
	case err == nil:
		return &controlpb.Response{Result: &controlpb.Response_LoginMesh{LoginMesh: &controlpb.LoginMeshResponse{LoginUrl: url}}}
	case errors.Is(err, engine.ErrNotRunning), errors.Is(err, engine.ErrInvalidArgument):
		return engineFailure(err)
	}
	response := failure(CodeOperationFailed, err.Error())
	response.GetError().Retryable = true
	return response
}

func (d *Dispatcher) matchRule(match *controlpb.MatchRuleRequest) *controlpb.Response {
	request := engine.MatchRequest{Host: match.GetHost(), Port: 443, UDP: match.GetNetwork() == "udp"}
	switch port := match.GetPort(); {
	case port > 0xffff:
		return failure(CodeInvalidRequest, "match_rule needs a TCP or UDP port")
	case port != 0:
		request.Port = uint16(port)
	}
	if network := match.GetNetwork(); network != "" && network != "tcp" && network != "udp" {
		return failure(CodeInvalidRequest, "match_rule's network is tcp or udp")
	}
	if match.Source != nil {
		profile, response := d.draftProfile(match.GetConfigYaml(), match.GetComposition(), match.GetOverrides(), nil)
		if response != nil {
			return response
		}
		request.Draft = &profile
	}
	matched, err := d.engine.MatchRule(request)
	if err != nil {
		return engineFailure(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_MatchRule{MatchRule: matched}}
}

// draftProfile is a configuration the routing methods build on the side; unlike profile,
// it never touches the system proxy.
func (d *Dispatcher) draftProfile(configYAML string, source *controlpb.Composition, overrideYAML []string, options *controlpb.RunOptions) (admission.Profile, *controlpb.Response) {
	run, response := runOptions(options)
	if response != nil {
		return admission.Profile{}, response
	}
	return admission.Profile{
		Config:      []byte(configYAML),
		Composition: composition(source),
		Overrides:   overrides(overrideYAML),
		Options:     run,
	}, nil
}

func (d *Dispatcher) subscribe(ctx context.Context, caller peer.Identity, kind topic, level controlpb.LogLevel) *controlpb.Response {
	id, err := d.subscriptions.subscribe(connectionContext(ctx), caller, kind, level)
	if err != nil {
		return subscriptionFailure(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_Subscribe{Subscribe: &controlpb.SubscribeResponse{SubscriptionId: id}}}
}

func knownSort(sort controlpb.GroupSort) bool {
	_, ok := controlpb.GroupSort_name[int32(sort)]
	return ok
}

func (d *Dispatcher) info(caller peer.Identity) *controlpb.GetInfoResponse {
	info := &controlpb.GetInfoResponse{
		ProtocolVersion: product.ProtocolVersion,
		ServiceVersion:  product.Version,
		CoreVersion:     engine.Version(),
		State:           controlpb.RunState_RUN_STATE_STOPPED,
	}
	if d.engine != nil {
		info.CoreVersion, info.State = d.engine.CoreVersion(), d.engine.State()
	}
	switch {
	case d.host != nil:
		info.Ownership = d.host.Ownership(caller)
	case d.development:
		// The development socket only admits the user running the server.
		info.Ownership = controlpb.Ownership_OWNERSHIP_CALLER
	case d.hostMethods:
		info.Ownership = controlpb.Ownership_OWNERSHIP_AVAILABLE
	}
	return info
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
		{context.DeadlineExceeded, CodeTimeout, true},
		{context.Canceled, CodeCancelled, true},
		{operation.ErrUnresponsive, CodeUnresponsive, true},
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

// hostFailure maps the Windows service's refusals; anything else is an engine error.
func hostFailure(err error) *controlpb.Response {
	switch {
	case errors.Is(err, ErrNotClaimed):
		return failure(CodeNotClaimed, err.Error())
	case errors.Is(err, ErrOwnedByOtherUser):
		return failure(CodeOwnedByOtherUser, err.Error())
	case errors.Is(err, ErrRestarting):
		response := failure(CodeServiceRestarting, err.Error())
		response.GetError().Retryable = true
		return response
	}
	return engineFailure(err)
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
