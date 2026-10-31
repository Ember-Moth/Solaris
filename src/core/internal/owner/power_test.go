package owner

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/power"
	"github.com/Ember-Moth/Solaris/src/core/internal/sysproxy"
)

type powerMachine struct {
	*machine
	ready  atomic.Bool
	checks atomic.Int32
}

func (m *powerMachine) NetworkReady() bool { m.checks.Add(1); return m.ready.Load() }

type powerCore struct {
	host                    *Service
	gate                    operation.Gate
	running                 atomic.Bool
	resumes, suspends, fail atomic.Int32
	mu                      sync.Mutex
	profiles                []*controlpb.StartRequest
	block                   <-chan struct{}
	entered                 chan struct{}
}

func (c *powerCore) Running() bool { return c.running.Load() }

func (c *powerCore) ResumeContext(ctx context.Context, profile *controlpb.StartRequest) error {
	if err := c.gate.Lock(ctx); err != nil {
		return err
	}
	defer c.gate.Unlock()
	if !c.host.ResumeAllowed() {
		return nil
	}
	if c.running.Load() {
		return nil
	}
	c.resumes.Add(1)
	if c.entered != nil {
		select {
		case c.entered <- struct{}{}:
		default:
		}
	}
	if c.block != nil {
		select {
		case <-c.block:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	if c.fail.Add(-1) >= 0 {
		return errors.New("network is not ready yet")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	c.mu.Lock()
	c.profiles = append(c.profiles, proto.Clone(profile).(*controlpb.StartRequest))
	c.mu.Unlock()
	c.running.Store(true)
	return c.host.Applied(profile)
}

func (c *powerCore) SuspendContext(ctx context.Context) error {
	if err := c.gate.Lock(ctx); err != nil {
		return err
	}
	defer c.gate.Unlock()
	c.suspends.Add(1)
	c.running.Store(false)
	return c.host.Suspended()
}

func powered(t *testing.T, connected bool) (*Service, *powerCore, *powerMachine, string) {
	t.Helper()
	root := t.TempDir()
	m := &powerMachine{machine: newMachine()}
	m.ready.Store(true)
	m.sessions[1], m.sessions[2] = alice, bob
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	s, home, err := Open(context.Background(), root, bundled(t), m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	c := &powerCore{host: s}
	s.core = c
	s.powerDelays = []time.Duration{0, time.Millisecond, time.Millisecond}
	if connected {
		c.running.Store(true)
		if err := s.Applied(profile(true)); err != nil {
			t.Fatal(err)
		}
	}
	t.Cleanup(s.Close)
	return s, c, m, home
}

func eventually(t *testing.T, test func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !test() {
		if time.Now().After(deadline) {
			t.Fatal("condition did not become true")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestSleepAndWakeRestoreTheSameProfileOnce(t *testing.T) {
	s, core, _, home := powered(t, true)
	s.PowerChanged(power.Event{Cycle: 1, Sleeping: true})
	eventually(t, func() bool { return core.suspends.Load() == 1 && !core.Running() })
	if !(kept{home}).wasRunning() {
		t.Fatal("sleep was kept as a user disconnect")
	}
	s.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool {
		state, _ := s.RecoveryStatus()
		return core.Running() && state == controlpb.RecoveryState_RECOVERY_STATE_NONE
	})
	s.PowerChanged(power.Event{Cycle: 1})
	if core.resumes.Load() != 1 || !s.SystemProxyEnabled() {
		t.Fatal("wake duplicated or lost the proxy")
	}
	core.mu.Lock()
	defer core.mu.Unlock()
	if len(core.profiles) != 1 || !proto.Equal(core.profiles[0], profile(true)) {
		t.Fatal("wake changed the profile")
	}
}

func TestWakeDoesNotConnectAnIdleUser(t *testing.T) {
	s, core, _, _ := powered(t, false)
	s.PowerChanged(power.Event{Cycle: 1, Sleeping: true})
	eventually(t, func() bool { return core.suspends.Load() == 1 })
	s.PowerChanged(power.Event{Cycle: 1})
	state, _ := s.RecoveryStatus()
	if core.resumes.Load() != 0 || state != controlpb.RecoveryState_RECOVERY_STATE_NONE {
		t.Fatal("idle user was connected")
	}
}

func TestWakeRetriesTransientFailuresWithinOneCycle(t *testing.T) {
	s, core, _, _ := powered(t, true)
	core.fail.Store(2)
	s.PowerChanged(power.Event{Cycle: 1}) // no preceding sleep notification
	eventually(t, func() bool {
		state, _ := s.RecoveryStatus()
		return core.resumes.Load() == 3 && state == controlpb.RecoveryState_RECOVERY_STATE_NONE
	})
	if !core.Running() {
		t.Fatal("transient recovery never connected")
	}
}

func TestWakeReportsNetworkFailureAfterFiniteAttempts(t *testing.T) {
	s, core, machine, _ := powered(t, true)
	machine.ready.Store(false)
	s.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool {
		state, _ := s.RecoveryStatus()
		return state == controlpb.RecoveryState_RECOVERY_STATE_FAILED
	})
	_, reason := s.RecoveryStatus()
	if reason != "network_unavailable" || machine.checks.Load() != 3 || core.resumes.Load() != 0 {
		t.Fatalf("reason=%s checks=%d", reason, machine.checks.Load())
	}
}

func TestUserDisconnectCancelsPendingWakeRecovery(t *testing.T) {
	s, core, machine, home := powered(t, true)
	machine.ready.Store(false)
	s.powerDelays = []time.Duration{0, time.Hour}
	s.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool { return machine.checks.Load() == 1 })
	if err := s.StopRequested(as(alice, 1)); err != nil {
		t.Fatal(err)
	}
	if err := s.Stopped(); err != nil {
		t.Fatal(err)
	}
	machine.ready.Store(true)
	s.PowerChanged(power.Event{Cycle: 2, Sleeping: true})
	s.PowerChanged(power.Event{Cycle: 2})
	state, _ := s.RecoveryStatus()
	if core.resumes.Load() != 0 || state != controlpb.RecoveryState_RECOVERY_STATE_NONE || (kept{home}).wasRunning() {
		t.Fatal("wake undid the user's disconnect")
	}
}

func TestSignOutCancelsResumeBeforeTheCoreConnects(t *testing.T) {
	s, core, _, _ := powered(t, true)
	release := make(chan struct{})
	core.block, core.entered = release, make(chan struct{}, 2)
	s.PowerChanged(power.Event{Cycle: 1})
	select {
	case <-core.entered:
	case <-time.After(time.Second):
		t.Fatal("resume did not start")
	}
	s.SessionChanged(1, false)
	eventually(t, func() bool { s.mu.Lock(); defer s.mu.Unlock(); return len(s.sessionJobs) == 0 })
	close(release)
	if core.Running() || s.ResumeAllowed() {
		t.Fatal("resume survived sign-out")
	}
	s.SessionChanged(1, true)
	eventually(t, func() bool { return core.Running() })
}

func TestOwnerChangeCancelsTheFormerOwnersRecovery(t *testing.T) {
	s, core, machine, _ := powered(t, true)
	machine.ready.Store(false)
	s.powerDelays = []time.Duration{0, time.Hour}
	s.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool { return machine.checks.Load() == 1 })
	if err := s.Claim(as(bob, 2)); !errors.Is(err, control.ErrRestarting) {
		t.Fatal(err)
	}
	machine.ready.Store(true)
	s.PowerChanged(power.Event{Cycle: 2})
	if core.resumes.Load() != 0 || s.ResumeAllowed() {
		t.Fatal("the former owner resumed after take-over")
	}
}

func TestInterruptedAutomaticResumeDoesNotCreateACrashLoop(t *testing.T) {
	s, _, machine, home := powered(t, true)
	if err := (kept{home}).markResumeAttempt(); err != nil {
		t.Fatal(err)
	}
	s.Close() // as if the service was replaced before the automatic attempt completed
	restarted, core, _ := start(t, s.root, machine.machine)
	t.Cleanup(restarted.Close)
	state, reason := restarted.RecoveryStatus()
	if len(core.resumed) != 0 || state != controlpb.RecoveryState_RECOVERY_STATE_FAILED || reason != "core_unresponsive" {
		t.Fatal("interrupted resume was retried automatically")
	}
	core.running = true // a successful explicit connection is allowed
	if err := restarted.Applied(profile(false)); err != nil {
		t.Fatal(err)
	}
	if (kept{home}).resumeInterrupted() || !restarted.ResumeAllowed() {
		t.Fatal("manual connection did not clear the guard")
	}
}

type proxyFailureMachine struct {
	*powerMachine
	block   atomic.Bool
	fail    atomic.Int32
	entered chan struct{}
	release chan struct{}
}

func (m *proxyFailureMachine) WriteProxy(session uint32, settings sysproxy.Settings) error {
	if settings == sysproxy.Core(7890) {
		if m.block.Swap(false) {
			close(m.entered)
			<-m.release
		}
		if m.fail.Add(-1) >= 0 {
			return errors.New("proxy settings temporarily unavailable")
		}
	}
	return m.machine.WriteProxy(session, settings)
}

func TestResumeGuardSurvivesUntilOSProxyApplicationFinishes(t *testing.T) {
	s, core, machine, home := powered(t, true)
	if err := core.SuspendContext(context.Background()); err != nil {
		t.Fatal(err)
	}
	platform := &proxyFailureMachine{powerMachine: machine, entered: make(chan struct{}), release: make(chan struct{})}
	platform.block.Store(true)
	s.platform = platform
	var once sync.Once
	release := func() { once.Do(func() { close(platform.release) }) }
	defer release()
	done := make(chan error, 1)
	go func() { done <- s.resume(context.Background(), profile(true)) }()
	select {
	case <-platform.entered:
	case <-time.After(time.Second):
		t.Fatal("proxy application did not start")
	}
	if !(kept{home}).resumeInterrupted() {
		t.Fatal("guard cleared before the OS operation finished")
	}
	release()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if (kept{home}).resumeInterrupted() {
		t.Fatal("successful resume retained its guard")
	}
}

func TestRecoveryRetriesTheProxyWhenTheCoreWasAlreadyStarted(t *testing.T) {
	s, core, machine, _ := powered(t, true)
	platform := &proxyFailureMachine{powerMachine: machine}
	platform.fail.Store(1)
	s.platform = platform
	s.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool {
		state, _ := s.RecoveryStatus()
		return state == controlpb.RecoveryState_RECOVERY_STATE_NONE
	})
	if core.resumes.Load() != 2 || !s.SystemProxyEnabled() {
		t.Fatal("partial resume was treated as complete")
	}
}
