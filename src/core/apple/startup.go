//go:build cgo

package main

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
)

// A UI can disappear between bringing up NE and sending its configuration. Do not
// leave an empty TUN swallowing traffic forever. Any provider message proves the UI
// is still present, so long batches of profile inspection get a fresh idle budget.
// Start or detach cancels the guard permanently; running tunnels need no UI heartbeat.
func guardInitialConfiguration(limit time.Duration, running func() bool, fail func(operation.Failure)) (touch func(), stop func()) {
	var finished atomic.Bool
	started := time.Now()
	var lastContact atomic.Int64
	var timer *time.Timer
	var check func()
	check = func() {
		if finished.Load() {
			return
		}
		idle := time.Since(started) - time.Duration(lastContact.Load())
		if idle < limit {
			timer.Reset(limit - idle)
			return
		}
		if finished.CompareAndSwap(false, true) && !running() {
			fail(operation.Failure{Method: "await_initial_configuration", Cause: context.DeadlineExceeded})
		}
	}
	timer = time.AfterFunc(limit, check)
	return func() { lastContact.Store(time.Since(started).Nanoseconds()) }, func() {
		finished.Store(true)
		timer.Stop()
	}
}
