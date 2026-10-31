//go:build cgo

package main

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
)

// A UI can disappear between bringing up NE and sending its configuration. Do not
// leave an empty TUN swallowing traffic forever. Normal startup has two minutes for
// profile inspection, validation and Start; the guard is cancelled after Start or detach.
func guardInitialConfiguration(limit time.Duration, running func() bool, fail func(operation.Failure)) func() {
	var finished atomic.Bool
	timer := time.AfterFunc(limit, func() {
		if finished.CompareAndSwap(false, true) && !running() {
			fail(operation.Failure{Method: "await_initial_configuration", Cause: context.DeadlineExceeded})
		}
	})
	return func() {
		finished.Store(true)
		timer.Stop()
	}
}
