//go:build cgo

package main

import (
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
)

func TestInitialConfigurationGuard(t *testing.T) {
	for _, mode := range []string{"abandoned", "configured", "detached"} {
		t.Run(mode, func(t *testing.T) {
			failed := make(chan operation.Failure, 1)
			_, done := guardInitialConfiguration(5*time.Millisecond, func() bool {
				return mode == "configured"
			}, func(f operation.Failure) { failed <- f })
			defer done()
			if mode == "detached" {
				done()
			}
			wait := 50 * time.Millisecond
			if mode == "abandoned" {
				wait = 2 * time.Second
			}
			select {
			case failure := <-failed:
				if mode != "abandoned" || failure.Method != "await_initial_configuration" {
					t.Fatal(mode, failure)
				}
			case <-time.After(wait):
				if mode == "abandoned" {
					t.Fatal("empty tunnel was not timed out")
				}
			}
		})
	}
}

func TestPreparationActivityRefreshesTheIdleBudget(t *testing.T) {
	failed := make(chan operation.Failure, 1)
	touch, done := guardInitialConfiguration(time.Second, func() bool { return false },
		func(f operation.Failure) { failed <- f })
	defer done()
	time.Sleep(600 * time.Millisecond)
	touch()
	select {
	case <-failed:
		t.Fatal("active preparation used the original deadline")
	case <-time.After(600 * time.Millisecond):
	}
	select {
	case <-failed: // the UI disappeared after its last message
	case <-time.After(2 * time.Second):
		t.Fatal("abandoned preparation did not expire")
	}
}
