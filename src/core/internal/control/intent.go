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
// service with was_running still true after its owner explicitly disconnected.
func (d *Dispatcher) prepareStop(ctx context.Context, caller peer.Identity) (requestIntent, error) {
	return operation.Run(d.intents, ctx, "record_stop", 5*time.Second, 0,
		func(work context.Context) (requestIntent, error) {
			d.intentMu.Lock()
			defer d.intentMu.Unlock()
			if err := operation.Check(work); err != nil {
				return requestIntent{}, err
			}
			var keepError error
			if d.host != nil {
				if err := d.host.Admit(caller); err != nil {
					return requestIntent{}, err
				}
				keepError = d.host.StopRequested(caller)
				if errors.Is(keepError, ErrNotClaimed) || errors.Is(keepError, ErrOwnedByOtherUser) || errors.Is(keepError, ErrRestarting) {
					return requestIntent{}, keepError
				}
			}
			return requestIntent{generation: d.stopGeneration.Add(1), stopError: keepError}, nil
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

// commitApplied orders the running-state write against a user's Stop. If Stop won while
// the library was applying a profile, finish stopping it here even if the queued Stop's
// caller disconnected. A late Start/Reload must never turn auto-resume back on.
func (d *Dispatcher) commitApplied(ctx context.Context, start *controlpb.StartRequest) error {
	d.intentMu.Lock()
	if err := d.operationActive(ctx); err != nil {
		d.intentMu.Unlock()
		return err
	}
	if !d.currentIntent(ctx) {
		d.intentMu.Unlock()
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
	defer d.intentMu.Unlock()
	if d.host != nil {
		return d.host.Applied(start)
	}
	return nil
}
