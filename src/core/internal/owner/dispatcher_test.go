package owner

import (
	"context"
	"io"
	"log/slog"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

// stubEngine is the core as the dispatcher sees it, without mihomo. Start waits for
// release when there is one.
type stubEngine struct {
	control.Engine
	running atomic.Bool
	entered chan struct{}
	release chan struct{}
}

func (e *stubEngine) State() controlpb.RunState {
	if e.running.Load() {
		return controlpb.RunState_RUN_STATE_RUNNING
	}
	return controlpb.RunState_RUN_STATE_STOPPED
}

func (e *stubEngine) Start(admission.Profile) (*controlpb.CompositionReport, error) {
	if e.entered != nil {
		close(e.entered)
		<-e.release
	}
	e.running.Store(true)
	return nil, nil
}

func (e *stubEngine) Stop() error { e.running.Store(false); return nil }

// front opens alice's service in root behind the real dispatcher, as service_windows.go
// wires them. hung receives what the watchdog takes for a hung core.
func front(t *testing.T, root string, platform Platform, engine *stubEngine) (service *Service, dispatcher *control.Dispatcher, hung chan operation.Failure) {
	t.Helper()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	service, _, err := Open(context.Background(), root, bundled(t), platform, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	hung = make(chan operation.Failure, 1)
	dispatcher = control.New(control.Options{Engine: engine, HostMethods: true, Host: service,
		OnUnresponsive: func(failure operation.Failure) { hung <- failure }})
	t.Cleanup(func() { service.Close(); dispatcher.Close() })
	service.core = dispatcher
	return service, dispatcher, hung
}

func handle(t *testing.T, dispatcher *control.Dispatcher, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	t.Helper()
	request.ProtocolVersion = product.ProtocolVersion
	encoded, err := proto.Marshal(request)
	if err != nil {
		t.Error(err)
		return nil
	}
	response := &controlpb.Response{}
	if err := proto.Unmarshal(dispatcher.Handle(context.Background(), caller, encoded), response); err != nil {
		t.Error(err)
	}
	return response
}

// An owner disconnects while the OS is slow to take their system proxy. Their Stop keeps its
// intent at once: it waits neither for the proxy write nor for the Start that made it, and
// the watchdog takes nothing for a hung core. The Start then finds itself superseded and
// stops the core, so a replacement process would not bring the tunnel back.
func TestStopIsKeptWhileTheSystemProxyIsSlow(t *testing.T) {
	machine := newMachine()
	machine.sessions[1] = alice
	platform := &proxyFailureMachine{powerMachine: &powerMachine{machine: machine},
		entered: make(chan struct{}), release: make(chan struct{})}
	platform.block.Store(true)
	var once sync.Once
	release := func() { once.Do(func() { close(platform.release) }) }
	defer release()
	engine := &stubEngine{}
	root := t.TempDir()
	service, dispatcher, hung := front(t, root, platform, engine)
	home := Home(root, alice)

	started := make(chan *controlpb.Response, 1)
	go func() {
		started <- handle(t, dispatcher, as(alice, 1), &controlpb.Request{Id: 1, Method: &controlpb.Request_Start{
			Start: profile(true)}})
	}()
	<-platform.entered
	if !(kept{home}).wasRunning() {
		t.Fatal("the Start was not kept before its system proxy")
	}
	stopped := make(chan *controlpb.Response, 1)
	go func() {
		stopped <- handle(t, dispatcher, as(alice, 1), &controlpb.Request{Id: 2, Method: &controlpb.Request_Stop{
			Stop: &controlpb.StopRequest{}}})
	}()
	eventually(t, func() bool { return !(kept{home}).wasRunning() })
	if service.ResumeAllowed() {
		t.Fatal("the stop intent did not end automatic recovery")
	}
	release()
	if code := (<-started).GetError().GetCode(); code != control.CodeCancelled {
		t.Fatalf("superseded Start = %q", code)
	}
	if response := <-stopped; response.GetError() != nil {
		t.Fatal(response.GetError())
	}
	select {
	case failure := <-hung:
		t.Fatalf("%s was taken for a hung core", failure.Method)
	default:
	}
	if engine.running.Load() || service.SystemProxyEnabled() || (kept{home}).wasRunning() {
		t.Fatal("the tunnel outlived its owner's Stop")
	}
}

// A sign-out or a new power event cancels an automatic resume whose Start already entered
// the core. The resume still waits for that Start, so its crash-loop guard stays until the
// Start is over; a guard cleared early would let a replacement process resume a hung Start
// again.
func TestCancelledResumeKeepsItsGuardWhileTheStartRuns(t *testing.T) {
	root := t.TempDir()
	machine := newMachine()
	machine.sessions[1] = alice
	home := kept{Home(root, alice)}
	if err := os.MkdirAll(home.home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := home.saveProfile(profile(false)); err != nil {
		t.Fatal(err)
	}
	if err := home.setWasRunning(true); err != nil {
		t.Fatal(err)
	}
	engine := &stubEngine{entered: make(chan struct{}), release: make(chan struct{})}
	service, dispatcher, _ := front(t, root, machine, engine)

	ctx, cancel := context.WithCancel(context.Background())
	resumed := make(chan error, 1)
	go func() { resumed <- service.resume(ctx, profile(false)) }()
	<-engine.entered
	cancel()
	select {
	case err := <-resumed:
		t.Fatalf("the resume ended (%v) while its Start still ran", err)
	case <-time.After(100 * time.Millisecond):
	}
	if !home.resumeInterrupted() {
		t.Fatal("the crash-loop guard went while the Start still ran")
	}
	close(engine.release)
	if err := <-resumed; err != nil {
		t.Fatal(err)
	}
	if home.resumeInterrupted() || !dispatcher.Running() {
		t.Fatal("the finished resume kept its guard")
	}
}
