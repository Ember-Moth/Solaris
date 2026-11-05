package control

import (
	"context"
	"errors"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

type intentKey struct{}

type requestIntent struct {
	generation uint64
	stopError  error
}

// prepareStop does not wait behind the core: otherwise a hung reload could restart the
// service with was_running still true after its owner explicitly disconnected. Nor does
// it wait behind a slow Applied: the host supersedes earlier starts, and checks them,
// under a lock it never holds across the OS or the core. Recording the intent waits for
// small writes only, and its watchdog fires only if one of those hangs.
func (d *Dispatcher) prepareStop(ctx context.Context, caller peer.Identity) (requestIntent, error) {
	return operation.Run(d.intents, ctx, "record_stop", 5*time.Second, 0,
		func(work context.Context) (requestIntent, error) {
			if tooLate(work) {
				return requestIntent{}, context.DeadlineExceeded
			}
			if d.host == nil {
				return requestIntent{generation: d.stopGeneration.Add(1)}, nil
			}
			var generation uint64
			keepError := d.host.StopRequested(caller, func() { generation = d.stopGeneration.Add(1) })
			if errors.Is(keepError, ErrNotClaimed) || errors.Is(keepError, ErrOwnedByOtherUser) || errors.Is(keepError, ErrRestarting) {
				return requestIntent{}, keepError
			}
			return requestIntent{generation: generation, stopError: keepError}, nil
		})
}

func (d *Dispatcher) currentIntent(ctx context.Context) bool {
	intent, ok := ctx.Value(intentKey{}).(requestIntent)
	return !ok || intent.generation == d.stopGeneration.Load()
}

func (d *Dispatcher) operationActive(ctx context.Context) error {
	if d.unresponsive.Load() {
		return operation.ErrUnresponsive
	}
	return operation.Check(ctx)
}

// commitApplied orders the running-state write against a user's Stop: the host keeps the
// start only if it is still current, which it checks under the lock StopRequested takes to
// supersede starts. If Stop won while the library or the host was applying a profile,
// finish stopping it here even if the queued Stop's caller disconnected. A late
// Start/Reload must never turn auto-resume back on.
func (d *Dispatcher) commitApplied(ctx context.Context, start *controlpb.StartRequest) error {
	if err := d.operationActive(ctx); err != nil {
		return err
	}
	current := func() bool { return d.currentIntent(ctx) }
	var err error
	if d.host != nil && current() {
		err = d.host.Applied(start, current)
	}
	if current() {
		return err
	}
	if err := d.engine.Stop(); err != nil {
		return err
	}
	if d.host != nil {
		if err := d.host.Stopped(); err != nil {
			return err
		}
	}
	return context.Canceled
}
