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
			done := guardInitialConfiguration(5*time.Millisecond, func() bool {
				return mode == "configured"
			}, func(f operation.Failure) { failed <- f })
			defer done()
			if mode == "detached" {
				done()
			}
			select {
			case failure := <-failed:
				if mode != "abandoned" || failure.Method != "await_initial_configuration" {
					t.Fatal(mode, failure)
				}
			case <-time.After(50 * time.Millisecond):
				if mode == "abandoned" {
					t.Fatal("empty tunnel was not timed out")
				}
			}
		})
	}
}
