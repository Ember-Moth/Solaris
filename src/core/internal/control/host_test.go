package control

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// fakeHost owns the service for owner and records what the dispatcher tells it.
type fakeHost struct {
	owner       string
	claimErr    error
	applied     []*controlpb.StartRequest
	stopped     int
	suspended   int
	systemProxy bool
	proxyErr    error
	// tookOver lists who asked to take over; the owner package decides who may.
	tookOver []string
}

func (h *fakeHost) Ownership(caller peer.Identity) controlpb.Ownership {
	if caller.UserID == h.owner {
		return controlpb.Ownership_OWNERSHIP_CALLER
	}
	return controlpb.Ownership_OWNERSHIP_OTHER
}

func (h *fakeHost) Admit(caller peer.Identity) error {
	switch caller.UserID {
	case h.owner:
		return nil
	case "idle":
		return ErrNotClaimed
	}
	return ErrOwnedByOtherUser
}

func (h *fakeHost) Claim(peer.Identity) error { return h.claimErr }

func (h *fakeHost) TakeOver(caller peer.Identity, stop func() error) error {
	h.tookOver = append(h.tookOver, caller.UserID)
	if err := stop(); err != nil {
		return err
	}
	return h.claimErr
}

func (h *fakeHost) Applied(start *controlpb.StartRequest) error {
	h.applied = append(h.applied, start)
	h.systemProxy = start.GetOptions().GetSystemProxyEnabled()
	return h.proxyErr
}

func (h *fakeHost) StopRequested(caller peer.Identity) error { return h.Admit(caller) }

func (h *fakeHost) ResumeAllowed() bool { return true }

func (h *fakeHost) Stopped() error {
	h.stopped++
	h.systemProxy = false
	return nil
}

func (h *fakeHost) Suspended() error {
	h.suspended++
	h.systemProxy = false
	return nil
}

func (h *fakeHost) SetSystemProxy(enabled bool) error {
	if h.proxyErr != nil {
		return h.proxyErr
	}
	h.systemProxy = enabled
	return nil
}

func (h *fakeHost) SystemProxyEnabled() bool { return h.systemProxy }

func claimRequest(r *controlpb.Request) {
	r.Method = &controlpb.Request_Claim{Claim: &controlpb.ClaimRequest{}}
}

func takeOverRequest(r *controlpb.Request) {
	r.Method = &controlpb.Request_Claim{Claim: &controlpb.ClaimRequest{TakeOver: true}}
}

func getStatus(r *controlpb.Request) {
	r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
}

// Only the owner reaches the core; everyone may ask GetInfo and Claim (§5.4).
func TestHostAdmitsTheOwner(t *testing.T) {
	host := &fakeHost{owner: "S-1-5-21-1"}
	dispatcher := New(Options{Engine: unlinked{}, HostMethods: true, Host: host})
	t.Cleanup(dispatcher.Close)
	owner, other, idle := peer.Identity{UserID: "S-1-5-21-1"}, peer.Identity{UserID: "S-1-5-21-2"}, peer.Identity{UserID: "idle"}
	ctx := context.Background()

	if got := callAs(t, dispatcher, ctx, other, request(1, getInfo)).GetGetInfo().GetOwnership(); got != controlpb.Ownership_OWNERSHIP_OTHER {
		t.Fatalf("other's ownership = %v", got)
	}
	if got := callAs(t, dispatcher, ctx, owner, request(2, getInfo)).GetGetInfo().GetOwnership(); got != controlpb.Ownership_OWNERSHIP_CALLER {
		t.Fatalf("owner's ownership = %v", got)
	}
	for id, method := range []func(*controlpb.Request){getStatus, stopRequest, systemProxyRequest(false)} {
		expectError(t, callAs(t, dispatcher, ctx, other, request(uint64(10+id), method)), uint64(10+id), CodeOwnedByOtherUser)
		expectError(t, callAs(t, dispatcher, ctx, idle, request(uint64(20+id), method)), uint64(20+id), CodeNotClaimed)
	}
	expectOK(t, callAs(t, dispatcher, ctx, other, request(30, claimRequest)))

	host.claimErr = ErrRestarting
	response := callAs(t, dispatcher, ctx, other, request(31, claimRequest))
	expectError(t, response, 31, CodeServiceRestarting)
	if !response.GetError().GetRetryable() {
		t.Fatal("service_restarting is not retryable")
	}
	host.claimErr = ErrOwnedByOtherUser
	expectError(t, callAs(t, dispatcher, ctx, other, request(32, claimRequest)), 32, CodeOwnedByOtherUser)
}

// A service without an owner has no engine yet, and answers GetInfo all the same.
func TestHostWithoutAnEngine(t *testing.T) {
	host := &fakeHost{owner: "S-1-5-21-1"}
	dispatcher := New(Options{HostMethods: true, Host: host})
	t.Cleanup(dispatcher.Close)
	info := callAs(t, dispatcher, context.Background(), peer.Identity{UserID: "idle"}, request(1, getInfo)).GetGetInfo()
	if info.GetCoreVersion() != engine.Version() || info.GetState() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("info = %v", info)
	}
	// Even an owner the host lets through finds no engine until the new process opens it.
	expectError(t, callAs(t, dispatcher, context.Background(), peer.Identity{UserID: "S-1-5-21-1"}, request(2, getStatus)), 2, CodeNotClaimed)
	if dispatcher.Running() {
		t.Fatal("running without an engine")
	}
}

// Start, Reload and Resume hand the host what the core took; Stop and Suspend tell it the
// tunnel went down, for good or until the owner signs in again.
func TestHostKeepsWhatTheCoreRuns(t *testing.T) {
	host := &fakeHost{owner: "S-1-5-21-1"}
	dispatcher := New(Options{Engine: core, HostMethods: true, Host: host})
	t.Cleanup(func() {
		dispatcher.Close()
		_ = core.Stop()
	})
	owner := peer.Identity{UserID: "S-1-5-21-1"}
	ctx := context.Background()
	port := porttest.Free(t)
	withProxy := &controlpb.RunOptions{SystemProxyEnabled: true, MixedPort: uint32(port)}

	expectOK(t, callAs(t, dispatcher, ctx, owner, request(1, startRequest(profileYAML(port), withProxy))))
	if len(host.applied) != 1 || host.applied[0].GetConfigYaml() != profileYAML(port) || !dispatcher.Running() {
		t.Fatalf("applied %v", host.applied)
	}
	if status := callAs(t, dispatcher, ctx, owner, request(2, getStatus)).GetGetStatus(); !status.GetSystemProxyEnabled() {
		t.Fatalf("status = %v", status)
	}
	expectOK(t, callAs(t, dispatcher, ctx, owner, request(3, reloadRequest(profileYAML(port), &controlpb.RunOptions{MixedPort: uint32(port)}))))
	want := &controlpb.StartRequest{
		Source:  &controlpb.StartRequest_ConfigYaml{ConfigYaml: profileYAML(port)},
		Options: &controlpb.RunOptions{MixedPort: uint32(port)},
	}
	if len(host.applied) != 2 || !proto.Equal(host.applied[1], want) {
		t.Fatalf("reload applied %v", host.applied)
	}
	expectOK(t, callAs(t, dispatcher, ctx, owner, request(4, systemProxyRequest(true))))
	if !host.systemProxy {
		t.Fatal("the system proxy is off")
	}
	host.proxyErr = errors.New("no session")
	expectError(t, callAs(t, dispatcher, ctx, owner, request(5, systemProxyRequest(false))), 5, CodeOperationFailed)
	host.proxyErr = nil

	if err := dispatcher.Suspend(); err != nil || host.suspended != 1 || dispatcher.Running() {
		t.Fatalf("suspend: %v, %d suspended", err, host.suspended)
	}
	if err := dispatcher.Resume(host.applied[0]); err != nil || !dispatcher.Running() || len(host.applied) != 3 {
		t.Fatalf("resume: %v, applied %d", err, len(host.applied))
	}
	// Resuming a running core leaves it alone.
	if err := dispatcher.Resume(host.applied[0]); err != nil || len(host.applied) != 3 {
		t.Fatalf("second resume: %v, applied %d", err, len(host.applied))
	}
	expectOK(t, callAs(t, dispatcher, ctx, owner, request(6, stopRequest)))
	if host.stopped != 1 || dispatcher.Running() {
		t.Fatalf("stopped %d times", host.stopped)
	}
}

// Claim leaves the core to its owner; a take-over (decision D5b) has the host stop it, under
// the lock that Claim already holds.
func TestHostTakeOver(t *testing.T) {
	host := &fakeHost{owner: "S-1-5-21-1"}
	dispatcher := New(Options{Engine: core, HostMethods: true, Host: host})
	t.Cleanup(func() {
		dispatcher.Close()
		_ = core.Stop()
	})
	owner, admin := peer.Identity{UserID: "S-1-5-21-1"}, peer.Identity{UserID: "S-1-5-21-2", Administrator: true}
	ctx := context.Background()
	port := porttest.Free(t)
	expectOK(t, callAs(t, dispatcher, ctx, owner, request(1, startRequest(profileYAML(port), &controlpb.RunOptions{MixedPort: uint32(port)}))))

	host.claimErr = ErrOwnedByOtherUser
	expectError(t, callAs(t, dispatcher, ctx, admin, request(2, claimRequest)), 2, CodeOwnedByOtherUser)
	if len(host.tookOver) != 0 || !dispatcher.Running() {
		t.Fatalf("a claim took over for %v", host.tookOver)
	}
	host.claimErr = ErrRestarting
	expectError(t, callAs(t, dispatcher, ctx, admin, request(3, takeOverRequest)), 3, CodeServiceRestarting)
	if len(host.tookOver) != 1 || host.tookOver[0] != admin.UserID || dispatcher.Running() {
		t.Fatalf("took over for %v, running %v", host.tookOver, dispatcher.Running())
	}
	// Taking over the development server is claiming it: its user owns it already.
	expectOK(t, call(t, development(t), request(4, takeOverRequest)))
}

// The development server's only user is its owner.
func TestDevelopmentOwnership(t *testing.T) {
	info := call(t, development(t), request(1, getInfo)).GetGetInfo()
	if info.GetOwnership() != controlpb.Ownership_OWNERSHIP_CALLER {
		t.Fatalf("ownership = %v", info.GetOwnership())
	}
}
