package main

import (
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/power"
)

// Register for process-local notifications only; never put the CI runner to sleep.
func TestPowerNotificationRegistration(t *testing.T) {
	queue := power.NewQueue()
	watcher, err := watchPower(queue)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = watcher.Close() })
	watcher.notify(power.Suspend)
	watcher.notify(power.ResumeAutomatic)
	if got := queue.Latest(); got.Cycle != 1 || got.Sleeping {
		t.Fatal(got)
	}
	if err := watcher.Close(); err != nil {
		t.Fatal(err)
	}
	watcher.notify(power.Suspend)
	if queue.Latest().Sleeping {
		t.Fatal("notification survived unregister")
	}
}
