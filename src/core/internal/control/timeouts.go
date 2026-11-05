package control

import (
	"context"
	"sync"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

const (
	CodeTimeout      = "timeout"
	CodeCancelled    = "cancelled"
	CodeUnresponsive = "core_unresponsive"
)

type connectionKey struct{}

// minimumWork is the least time a mutation needs left as it starts. The watchdog takes one
// that outlives its deadline for a hung core, and the UI may pass a deadline only a
// millisecond away, so a mutation with less time left than this never starts and times
// out instead.
const minimumWork = time.Second

// tooLate says whether work that the watchdog times without grace should no longer start.
func tooLate(work context.Context) bool {
	deadline, ok := work.Deadline()
	return ok && time.Until(deadline) < minimumWork
}

// hostBudget is bounded independently of anything the caller asks for. The core gets a
// smaller budget, and the UI leaves another margin around this host budget.
func hostBudget(method string) time.Duration {
	switch method {
	case "validate", "start", "reload", "inspect_source", "preview_group", "match_rule", "convert_to_plan", "export_plan":
		return 55 * time.Second
	case "stop", "claim", "set_system_proxy", "login_mesh":
		return 25 * time.Second
	case "health_check", "test_delay", "update_provider", "register_warp":
		return 110 * time.Second
	case "list_vpn_gate":
		return 70 * time.Second
	default:
		return 12 * time.Second
	}
}

func requestBudget(request *controlpb.Request) time.Duration {
	budget := hostBudget(methodName(request))
	if milliseconds := request.GetTimeoutMs(); milliseconds != 0 {
		budget = min(budget, time.Duration(milliseconds)*time.Millisecond)
	}
	if deadline := request.GetDeadlineUnixMs(); deadline != 0 {
		budget = min(budget, time.Until(time.UnixMilli(deadline)))
	}
	return budget
}

// serialized requests take the operation lock. Besides the mutations, that is every method
// which takes the engine's lifecycle lock: a read waiting behind a 50-second Start would
// outlive its own deadline inside the engine, and the watchdog would take it for a hung core.
func serialized(request *controlpb.Request) bool {
	switch request.Method.(type) {
	case *controlpb.Request_Claim, *controlpb.Request_Validate, *controlpb.Request_Start,
		*controlpb.Request_UploadGeo, *controlpb.Request_GetGeoUpdate, *controlpb.Request_ListGeoCategories,
		*controlpb.Request_Reload, *controlpb.Request_Stop, *controlpb.Request_SetSystemProxy,
		*controlpb.Request_SetMode, *controlpb.Request_SelectProxy, *controlpb.Request_UpdateProvider,
		*controlpb.Request_CloseConnection, *controlpb.Request_CloseAllConnections,
		*controlpb.Request_FlushFakeIp, *controlpb.Request_FlushDnsCache,
		*controlpb.Request_InspectSource, *controlpb.Request_PreviewGroup,
		*controlpb.Request_MatchRule, *controlpb.Request_ConvertToPlan, *controlpb.Request_ExportPlan:
		return true
	default:
		return false
	}
}

func (d *Dispatcher) fail(failure operation.Failure) {
	d.unresponsive.Store(true)
	d.failOnce.Do(func() {
		if d.onUnresponsive != nil {
			go d.onUnresponsive(failure)
		}
	})
}

func (d *Dispatcher) execute(callerCtx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	_, stopping := request.Method.(*controlpb.Request_Stop)
	if d.unresponsive.Load() && !stopping {
		return engineFailure(operation.ErrUnresponsive)
	}
	budget := requestBudget(request)
	margin := min(5*time.Second, budget/5)
	executor, grace := d.reads, margin
	if serialized(request) {
		executor, grace = d.operations, 0
	}
	// A mutation without the time to run is refused outright, so a Stop that could not
	// stop the core does not keep its intent either.
	if budget <= 0 || grace == 0 && budget-margin < minimumWork {
		return engineFailure(context.DeadlineExceeded)
	}
	// Keep the connection's lifetime for subscriptions. The operation deadline must not
	// destroy a successfully created subscription when its request returns.
	ctx, cancel := context.WithTimeout(callerCtx, budget)
	defer cancel()
	detach := context.AfterFunc(d.lifetime, cancel)
	defer detach()
	if d.lifetime.Err() != nil {
		return engineFailure(context.Canceled)
	}
	intent := requestIntent{generation: d.stopGeneration.Load()}
	if stopping {
		var err error
		intent, err = d.prepareStop(ctx, caller)
		if err != nil {
			return hostFailure(err)
		}
	}
	ctx = context.WithValue(ctx, connectionKey{}, callerCtx)
	ctx = context.WithValue(ctx, intentKey{}, intent)
	response, err := operation.Run(executor, ctx, methodName(request), budget-margin, grace,
		func(work context.Context) (*controlpb.Response, error) {
			if d.unresponsive.Load() {
				return nil, operation.ErrUnresponsive
			}
			// Waiting for its turn may have used up a mutation's time.
			if grace == 0 && tooLate(work) {
				return nil, context.DeadlineExceeded
			}
			return d.dispatch(work, caller, request), nil
		})
	if err != nil {
		return engineFailure(err)
	}
	return response
}

func connectionContext(ctx context.Context) context.Context {
	if connection, ok := ctx.Value(connectionKey{}).(context.Context); ok {
		return connection
	}
	return ctx
}

// runLifecycle is the same admission and watchdog for service-initiated operations:
// sign-in, sign-out, sleep and wake must serialize with requests from the UI too.
// Cancelling ctx, or closing the dispatcher, withdraws an operation still waiting for its
// turn. One that has started is waited for, so the host learns how it ended: an automatic
// resume keeps its crash-loop guard until its Start is over, not just abandoned.
func (d *Dispatcher) runLifecycle(ctx context.Context, method string, run func(context.Context) error) error {
	if d.unresponsive.Load() {
		return operation.ErrUnresponsive
	}
	budget := hostBudget(method)
	queue, withdraw := context.WithCancel(context.WithoutCancel(ctx))
	defer withdraw()
	var mu sync.Mutex
	started := false
	cancel := func() {
		mu.Lock()
		defer mu.Unlock()
		if !started {
			withdraw()
		}
	}
	detach := context.AfterFunc(ctx, cancel)
	defer detach()
	detachLifetime := context.AfterFunc(d.lifetime, cancel)
	defer detachLifetime()
	if ctx.Err() != nil || d.lifetime.Err() != nil {
		return context.Canceled
	}
	queue = context.WithValue(queue, intentKey{}, requestIntent{generation: d.stopGeneration.Load()})
	_, err := operation.Run(d.operations, queue, method, budget-min(5*time.Second, budget/5), 0,
		func(work context.Context) (struct{}, error) {
			mu.Lock()
			started = queue.Err() == nil
			mu.Unlock()
			switch {
			case !started:
				return struct{}{}, context.Canceled
			case d.unresponsive.Load():
				return struct{}{}, operation.ErrUnresponsive
			case tooLate(work):
				return struct{}{}, context.DeadlineExceeded
			}
			return struct{}{}, run(work)
		})
	return err
}
