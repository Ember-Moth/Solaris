// Package operation bounds work that may enter a library which cannot be interrupted.
// Cancellation prevents queued work from starting. Once started, a transaction keeps its
// deadline even if its caller disconnects; exceeding it poisons the executor, so no second
// operation can enter the same process-global runtime. The host then replaces that process.
package operation

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"time"
)

var ErrUnresponsive = errors.New("the core is unresponsive; its host must recover")

// Gate is a cancellable mutex. Its zero value is ready to use.
type Gate struct {
	once sync.Once
	free chan struct{}
}

func (g *Gate) Lock(ctx context.Context) error {
	g.once.Do(func() { g.free = make(chan struct{}, 1) })
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case g.free <- struct{}{}:
		// Cancellation and acquiring the token may have become ready together.
		if err := ctx.Err(); err != nil {
			g.Unlock()
			return err
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (g *Gate) Unlock() { <-g.free }

// Failure contains no request data, only the method and its deadline failure.
type Failure struct {
	Method string
	Cause  error
}

// Executor limits work in flight, including work whose caller has already timed out.
// The slot belongs to the worker until it returns, never just until the caller returns.
type Executor struct {
	slots chan struct{}
	bad   atomic.Bool
	once  sync.Once
	hung  func(Failure)
}

func New(concurrency int, hung func(Failure)) *Executor {
	if concurrency < 1 {
		panic("operation concurrency must be positive")
	}
	return &Executor{slots: make(chan struct{}, concurrency), hung: hung}
}

func (e *Executor) Unresponsive() bool { return e.bad.Load() }

// Poison closes admission before notifying the host. It does not release a worker's
// locks or pretend that an uninterruptible library call has stopped.
func (e *Executor) Poison(method string, cause error) {
	e.bad.Store(true)
	e.once.Do(func() {
		if e.hung != nil {
			e.hung(Failure{Method: method, Cause: cause})
		}
	})
}

// Run waits for capacity within caller's lifetime and limit. The work receives a
// deadline bounded by both. A started transaction finishes independently of a lost
// connection; its own deadline remains enforced by the watchdog. Cancelled queued work
// never starts. A timed-out worker cannot be followed by another operation on this executor.
// grace is zero for non-cancellable mutations. Context-aware reads may use a short grace
// to unwind after their deadline, before the host is considered stuck.
func Run[T any](e *Executor, caller context.Context, method string, limit, grace time.Duration, work func(context.Context) (T, error)) (T, error) {
	var zero T
	if e.Unresponsive() {
		return zero, ErrUnresponsive
	}
	queued, cancelQueue := context.WithTimeout(caller, limit)
	defer cancelQueue()
	if err := queued.Err(); err != nil {
		return zero, err
	}
	select {
	case e.slots <- struct{}{}:
	case <-queued.Done():
		return zero, queued.Err()
	}
	if err := queued.Err(); err != nil {
		<-e.slots
		return zero, err
	}
	if e.Unresponsive() {
		<-e.slots
		return zero, ErrUnresponsive
	}
	deadline, _ := queued.Deadline()
	ctx, cancelWork := context.WithDeadline(context.WithoutCancel(caller), deadline)
	type outcome struct {
		value T
		err   error
	}
	done := make(chan outcome, 1)
	var started, finished atomic.Bool
	watchdog := time.AfterFunc(time.Until(deadline)+grace, func() {
		if started.Load() && !finished.Load() {
			e.Poison(method, context.DeadlineExceeded)
		}
	})
	// The worker owns the slot and deadline, even if Run returns on caller cancellation.
	go func() {
		defer func() { <-e.slots }()
		defer cancelWork()
		defer watchdog.Stop()
		if err := ctx.Err(); err != nil {
			finished.Store(true)
			done <- outcome{err: err}
			return
		}
		started.Store(true)
		value, err := work(ctx)
		if timeout := ctx.Err(); timeout != nil {
			if grace == 0 {
				e.Poison(method, timeout)
			}
			finished.Store(true)
			done <- outcome{err: timeout}
			return
		}
		finished.Store(true)
		done <- outcome{value, err}
	}()
	select {
	case result := <-done:
		return result.value, result.err
	case <-ctx.Done():
		// cancelWork also closes ctx when a completed worker returns. Prefer its result.
		select {
		case result := <-done:
			return result.value, result.err
		default:
		}
		if grace == 0 && started.Load() && !finished.Load() {
			e.Poison(method, ctx.Err())
		}
		return zero, ctx.Err()
	case <-caller.Done():
		return zero, caller.Err()
	}
}
