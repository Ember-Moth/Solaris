package control

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

type blockedCore struct {
	unlinked
	entered, release chan struct{}
	starts, stops    atomic.Int32
}

func (b *blockedCore) Start(admission.Profile) (*controlpb.CompositionReport, error) {
	b.starts.Add(1)
	close(b.entered)
	<-b.release
	return nil, nil
}

func (b *blockedCore) Stop() error { b.stops.Add(1); return nil }

type deadlineHost struct {
	fakeHost
	writes atomic.Int32
}

func (h *deadlineHost) Applied(*controlpb.StartRequest) error { h.writes.Add(1); return nil }

func TestExpiredRequestCannotStart(t *testing.T) {
	blocked := &blockedCore{entered: make(chan struct{}), release: make(chan struct{})}
	d := New(Options{Engine: blocked})
	defer d.Close()
	r := request(1, startRequest("proxies: []", nil))
	r.DeadlineUnixMs = time.Now().Add(-time.Second).UnixMilli()
	expectError(t, call(t, d, r), 1, CodeTimeout)
	if blocked.starts.Load() != 0 || d.unresponsive.Load() {
		t.Fatal("an expired request entered the engine or poisoned the host")
	}
}

func TestStopIntentSurvivesItsCallerTimeout(t *testing.T) {
	blocked := &blockedCore{entered: make(chan struct{}), release: make(chan struct{})}
	d := New(Options{Engine: blocked})
	defer d.Close()
	payload, _ := proto.Marshal(request(1, startRequest("proxies: []", nil)))
	first := make(chan []byte, 1)
	go func() { first <- d.Handle(context.Background(), peer.Identity{}, payload) }()
	<-blocked.entered
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	expectError(t, callAs(t, d, ctx, peer.Identity{}, request(2, stopRequest)), 2, CodeTimeout)
	if blocked.stops.Load() != 0 || d.unresponsive.Load() {
		t.Fatal("a cancelled queue entry ran or interrupted the preceding transaction")
	}
	close(blocked.release)
	expectError(t, decode(t, <-first), 1, CodeCancelled)
	if blocked.stops.Load() != 1 {
		t.Fatal("the late Start did not honor the owner's Stop intent")
	}
	expectOK(t, call(t, d, request(3, stopRequest)))
	if blocked.stops.Load() != 2 {
		t.Fatal("an explicit later Stop did not run")
	}
}

func TestClosingDispatcherCancelsQueuedRequests(t *testing.T) {
	blocked := &blockedCore{entered: make(chan struct{}), release: make(chan struct{})}
	d := New(Options{Engine: blocked})
	first, second := make(chan []byte, 1), make(chan []byte, 1)
	payload, _ := proto.Marshal(request(1, startRequest("proxies: []", nil)))
	go func() { first <- d.Handle(context.Background(), peer.Identity{}, payload) }()
	<-blocked.entered
	other, _ := proto.Marshal(request(2, startRequest("proxies: []", nil)))
	go func() { second <- d.Handle(context.Background(), peer.Identity{}, other) }()
	d.Close()
	select {
	case response := <-second:
		expectError(t, decode(t, response), 2, CodeCancelled)
	case <-time.After(time.Second):
		t.Fatal("closing did not cancel queued work")
	}
	close(blocked.release)
	<-first
	if blocked.starts.Load() != 1 {
		t.Fatal("another start ran while closing")
	}
}

func TestHungStartCannotPersistLateOrAdmitAnotherMutation(t *testing.T) {
	blocked := &blockedCore{entered: make(chan struct{}), release: make(chan struct{})}
	host := &deadlineHost{}
	hung := make(chan operation.Failure, 1)
	d := New(Options{Engine: blocked, Host: host, OnUnresponsive: func(f operation.Failure) { hung <- f }})
	defer d.Close()
	r := request(1, startRequest("proxies: []", nil))
	r.TimeoutMs = 30
	expectError(t, call(t, d, r), 1, CodeTimeout)
	select {
	case <-hung:
	case <-time.After(time.Second):
		t.Fatal("the host recovery was not requested")
	}
	expectError(t, call(t, d, request(2, stopRequest)), 2, CodeUnresponsive)
	close(blocked.release)
	// The worker can return late, but it must never write a successful running profile.
	time.Sleep(30 * time.Millisecond)
	if host.writes.Load() != 0 || blocked.stops.Load() != 0 {
		t.Fatal("a timed-out result committed or another mutation entered the runtime")
	}
}

func TestRequestDeadlineDoesNotExpireItsSubscription(t *testing.T) {
	d := New(Options{Engine: &sampler{}})
	defer d.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SubscribeTraffic{SubscribeTraffic: &controlpb.SubscribeTrafficRequest{}}
	})
	r.TimeoutMs = 40
	id := callAs(t, d, ctx, peer.Identity{}, r).GetSubscribe().GetSubscriptionId()
	if id == 0 {
		t.Fatal("subscription not created")
	}
	time.Sleep(60 * time.Millisecond)
	response := call(t, d, request(2, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_PollEvents{PollEvents: &controlpb.PollEventsRequest{SubscriptionId: id}}
	}))
	if response.GetPollEvents() == nil {
		t.Fatalf("subscription died with its request: %v", response)
	}
}
