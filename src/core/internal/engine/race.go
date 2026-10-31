//go:build race

package engine

import (
	"math"
	"runtime"
	"runtime/metrics"
	"time"
)

// raceEnabled says that this is a race-detector build.
const raceEnabled = true

// settleFinalizers waits for the finalizers that are due, so that what they did happens
// before the configuration that follows applies.
//
// mihomo closes what a configuration no longer uses from finalizers: an outbound a reload
// replaced closes once its last connection ends, and the close logs. Reading the log level
// there races with log.SetLevel at the start of the next hub.ApplyConfig. The race is
// inside mihomo, on an int, and the engine cannot order it without closing outbounds that
// connections still use, so only race-detector builds wait here (docs/maintenance.md §3).
func settleFinalizers() {
	deadline := time.Now().Add(5 * time.Second)
	// An object a finalizer can reach waits for that finalizer to run before it is
	// finalized itself, a collection later: a provider before its outbounds. Collect until
	// a collection finds nothing new.
	for previous := uint64(math.MaxUint64); time.Now().Before(deadline); {
		runtime.GC()
		queued := waitFinalizers(deadline)
		if queued == previous {
			break
		}
		previous = queued
	}
	// One goroutine runs the finalizers, so one more that closes a channel orders the ones
	// before it before whatever waits for the channel.
	done := make(chan struct{})
	// Past the tiny allocator, whose blocks may never be finalized.
	runtime.SetFinalizer(new([64]byte), func(*[64]byte) { close(done) })
	for waiting := true; waiting && time.Now().Before(deadline); {
		runtime.GC()
		select {
		case <-done:
			waiting = false
		case <-time.After(10 * time.Millisecond):
		}
	}
}

// waitFinalizers waits until every finalizer queued so far has run, and returns how many
// were ever queued.
func waitFinalizers(deadline time.Time) uint64 {
	samples := []metrics.Sample{
		{Name: "/gc/finalizers/queued:finalizers"},
		{Name: "/gc/finalizers/executed:finalizers"},
	}
	for {
		metrics.Read(samples)
		queued, executed := samples[0].Value.Uint64(), samples[1].Value.Uint64()
		if executed >= queued || time.Now().After(deadline) {
			return queued
		}
		time.Sleep(time.Millisecond)
	}
}
