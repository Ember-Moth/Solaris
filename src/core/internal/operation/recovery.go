package operation

import (
	"context"
	"fmt"
	"log/slog"
	"time"
)

// Terminate replaces a poisoned host instead of running another engine beside a hung
// mihomo call. A short delay lets the error response leave. A fatal panic also reaches
// Go's crash output; the Windows SCM can recover the service, and macOS owns the provider
// process and reclaims its tunnel. This function must only be installed by process hosts,
// never by an embedded caller which does not own the process.
func Terminate(failure Failure) {
	time.AfterFunc(250*time.Millisecond, func() {
		panic(fmt.Sprintf("core watchdog: %s did not finish within its deadline", failure.Method))
	})
	slog.Error("core watchdog expired; terminating host", "operation", failure.Method)
}

// Guard bounds host lifecycle work outside the dispatcher, notably attaching/detaching
// the Apple TUN. Cancelling it after completion does not cancel work that missed its deadline.
func Guard(method string, limit time.Duration, fail func(Failure)) func() {
	timer := time.AfterFunc(limit, func() { fail(Failure{Method: method, Cause: context.DeadlineExceeded}) })
	return func() { timer.Stop() }
}
