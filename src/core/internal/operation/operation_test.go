package operation

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

// A deadline whose cancellation callback has not run yet, as can happen when a runtime
// is busy or the OS coalesces timers. Checking Err alone would still admit this work.
type laggingDeadline struct {
	context.Context
	deadline time.Time
}

func (c laggingDeadline) Deadline() (time.Time, bool) { return c.deadline, true }

func TestExpiredClockPreventsWorkBeforeCancellationCallback(t *testing.T) {
	ctx := laggingDeadline{Context: context.Background(), deadline: time.Now().Add(-time.Second)}
	if ctx.Err() != nil {
		t.Fatal("test context already cancelled")
	}
	if !errors.Is(Check(ctx), context.DeadlineExceeded) {
		t.Fatal("missed expired clock")
	}
	var gate Gate
	if !errors.Is(gate.Lock(ctx), context.DeadlineExceeded) {
		t.Fatal("expired gate acquired")
	}
	executor := New(1, nil)
	called := false
	_, err := Run(executor, ctx, "expired", time.Second, 0, func(context.Context) (int, error) {
		called = true
		return 0, nil
	})
	if !errors.Is(err, context.DeadlineExceeded) || called || executor.Unresponsive() {
		t.Fatalf("err=%v called=%v unresponsive=%v", err, called, executor.Unresponsive())
	}
}

func TestQueuedCancellationDoesNotStartOrPoison(t *testing.T) {
	executor := New(1, nil)
	entered, release, ended := make(chan struct{}), make(chan struct{}), make(chan struct{})
	go func() {
		defer close(ended)
		_, _ = Run(executor, context.Background(), "start", time.Second, 0, func(context.Context) (int, error) {
			close(entered)
			<-release
			return 7, nil
		})
	}()
	<-entered
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	var called atomic.Bool
	_, err := Run(executor, ctx, "queued reload", time.Second, 0, func(context.Context) (int, error) {
		called.Store(true)
		return 0, nil
	})
	if !errors.Is(err, context.DeadlineExceeded) || called.Load() || executor.Unresponsive() {
		t.Fatalf("err=%v called=%v poisoned=%v", err, called.Load(), executor.Unresponsive())
	}
	close(release)
	<-ended
}

func TestStuckMutationReturnsAndRejectsLaterWork(t *testing.T) {
	notified := make(chan Failure, 2)
	executor := New(1, func(f Failure) { notified <- f })
	release, finished := make(chan struct{}), make(chan struct{})
	_, err := Run(executor, context.Background(), "start", 20*time.Millisecond, 0, func(context.Context) (int, error) {
		defer close(finished)
		<-release // a library that ignores cancellation
		return 42, nil
	})
	if !errors.Is(err, context.DeadlineExceeded) || !executor.Unresponsive() {
		t.Fatalf("err=%v poisoned=%v", err, executor.Unresponsive())
	}
	select {
	case failure := <-notified:
		if failure.Method != "start" || !errors.Is(failure.Cause, context.DeadlineExceeded) {
			t.Fatalf("failure=%+v", failure)
		}
	case <-time.After(time.Second):
		t.Fatal("host was not notified")
	}
	_, err = Run(executor, context.Background(), "stop", time.Second, 0, func(context.Context) (int, error) {
		t.Error("a second mutation entered the stuck runtime")
		return 0, nil
	})
	if !errors.Is(err, ErrUnresponsive) {
		t.Fatal(err)
	}
	close(release)
	<-finished
	if len(notified) != 0 {
		t.Fatal("the host was notified twice")
	}
}

func TestDisconnectDoesNotInterruptAnAdmittedTransaction(t *testing.T) {
	executor := New(1, nil)
	ctx, cancel := context.WithCancel(context.Background())
	entered, release, workerDone, callerDone := make(chan struct{}), make(chan struct{}), make(chan struct{}), make(chan error, 1)
	go func() {
		_, err := Run(executor, ctx, "start", time.Second, 0, func(work context.Context) (int, error) {
			defer close(workerDone)
			close(entered)
			<-release
			return 1, work.Err()
		})
		callerDone <- err
	}()
	<-entered
	cancel()
	if err := <-callerDone; !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	close(release)
	<-workerDone
	value, err := Run(executor, context.Background(), "next", time.Second, 0, func(context.Context) (int, error) { return 2, nil })
	if err != nil || value != 2 || executor.Unresponsive() {
		t.Fatalf("value=%v err=%v poisoned=%v", value, err, executor.Unresponsive())
	}
}

func TestDisconnectedStuckWorkerStillHasAWatchdog(t *testing.T) {
	notified := make(chan Failure, 1)
	executor := New(1, func(f Failure) { notified <- f })
	ctx, cancel := context.WithCancel(context.Background())
	entered, release := make(chan struct{}), make(chan struct{})
	go func() {
		_, _ = Run(executor, ctx, "reload", 30*time.Millisecond, 0, func(context.Context) (int, error) {
			close(entered)
			<-release
			return 0, nil
		})
	}()
	<-entered
	cancel()
	defer close(release)
	select {
	case <-notified:
	case <-time.After(time.Second):
		t.Fatal("disconnect disabled the watchdog")
	}
}

func TestCooperativeReadUnwindsWithoutPoisoning(t *testing.T) {
	executor := New(1, nil)
	_, err := Run(executor, context.Background(), "network read", 10*time.Millisecond, 50*time.Millisecond, func(ctx context.Context) (int, error) {
		<-ctx.Done()
		return 0, ctx.Err()
	})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	_, err = Run(executor, context.Background(), "next", time.Second, 0, func(context.Context) (int, error) { return 0, nil })
	if err != nil || executor.Unresponsive() {
		t.Fatalf("err=%v poisoned=%v", err, executor.Unresponsive())
	}
}

func TestGateCancelledWaiterCannotAcquireLater(t *testing.T) {
	var gate Gate
	if err := gate.Lock(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := gate.Lock(ctx); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	gate.Unlock()
	if err := gate.Lock(context.Background()); err != nil {
		t.Fatal(err)
	}
	gate.Unlock()
}
