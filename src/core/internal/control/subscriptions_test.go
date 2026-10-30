package control

import (
	"context"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

// sampler stands in for the engine: fixed samples, and a log feed the test drives.
type sampler struct {
	Engine
	mu       sync.Mutex
	emit     func(*controlpb.LogEntry)
	watching int
}

func (*sampler) Traffic() *controlpb.TrafficSample {
	return &controlpb.TrafficSample{UpBytesPerSecond: 1, DownBytesPerSecond: 2}
}

func (*sampler) Connections() *controlpb.ConnectionSnapshot {
	return &controlpb.ConnectionSnapshot{Connections: []*controlpb.Connection{{Id: "c1"}}}
}

func (s *sampler) WatchLogs(emit func(*controlpb.LogEntry)) func() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.emit = emit
	s.watching++
	return func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.emit = nil
		s.watching--
	}
}

func (s *sampler) log(level controlpb.LogLevel, message string) {
	s.mu.Lock()
	emit := s.emit
	s.mu.Unlock()
	if emit != nil {
		emit(&controlpb.LogEntry{Level: level, Message: message})
	}
}

func (s *sampler) watchers() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.watching
}

type clock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *clock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
}

func newTestSubscriptions(t *testing.T) (*subscriptions, *sampler, *clock) {
	t.Helper()
	engine := &sampler{}
	clock := &clock{now: time.Unix(1_000_000, 0)}
	subs := newSubscriptions(engine)
	subs.now = clock.Now
	subs.period = 5 * time.Millisecond
	t.Cleanup(subs.close)
	return subs, engine, clock
}

var alice = peer.Identity{UserID: "501"}

func pollUntil(t *testing.T, subs *subscriptions, id uint64, want int) []*controlpb.Event {
	t.Helper()
	var events []*controlpb.Event
	for deadline := time.Now().Add(5 * time.Second); len(events) < want; {
		got, _, err := subs.poll(alice, id, 0)
		if err != nil {
			t.Fatal(err)
		}
		events = append(events, got...)
		if time.Now().After(deadline) {
			t.Fatalf("got %d events, want %d", len(events), want)
		}
		time.Sleep(time.Millisecond)
	}
	return events
}

func TestTrafficAndConnectionSamples(t *testing.T) {
	subs, _, _ := newTestSubscriptions(t)
	traffic, err := subs.subscribe(context.Background(), alice, topicTraffic, 0)
	if err != nil {
		t.Fatal(err)
	}
	connections, err := subs.subscribe(context.Background(), alice, topicConnections, 0)
	if err != nil {
		t.Fatal(err)
	}
	if event := pollUntil(t, subs, traffic, 2)[0]; event.GetTraffic().GetDownBytesPerSecond() != 2 {
		t.Fatalf("traffic event = %v", event)
	}
	if event := pollUntil(t, subs, connections, 1)[0]; event.GetConnections().GetConnections()[0].GetId() != "c1" {
		t.Fatalf("connection event = %v", event)
	}
}

func TestFullBufferDropsTheOldest(t *testing.T) {
	subs, engine, _ := newTestSubscriptions(t)
	id, err := subs.subscribe(context.Background(), alice, topicLogs, controlpb.LogLevel_LOG_LEVEL_DEBUG)
	if err != nil {
		t.Fatal(err)
	}
	limit := capacity[topicLogs]
	for i := range limit + 10 {
		engine.log(controlpb.LogLevel_LOG_LEVEL_INFO, string(rune('a'+i%26)))
	}
	events, dropped, err := subs.poll(alice, id, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != limit || dropped != 10 {
		t.Fatalf("poll = %d events, %d dropped; want %d and 10", len(events), dropped, limit)
	}
	// The ten oldest went: the first remaining line is the eleventh.
	if first := events[0].GetLog().GetMessage(); first != string(rune('a'+10)) {
		t.Fatalf("first event = %q", first)
	}
	if _, dropped, _ := subs.poll(alice, id, 0); dropped != 0 {
		t.Fatalf("dropped is reported twice: %d", dropped)
	}
}

func TestPollTakesAtMostMaxEvents(t *testing.T) {
	subs, engine, _ := newTestSubscriptions(t)
	id, _ := subs.subscribe(context.Background(), alice, topicLogs, 0)
	for range 5 {
		engine.log(controlpb.LogLevel_LOG_LEVEL_ERROR, "x")
	}
	if events, _, _ := subs.poll(alice, id, 2); len(events) != 2 {
		t.Fatalf("poll(2) = %d events", len(events))
	}
	if events, _, _ := subs.poll(alice, id, 0); len(events) != 3 {
		t.Fatalf("the rest = %d events", len(events))
	}
}

func TestLogLevelFilter(t *testing.T) {
	subs, engine, _ := newTestSubscriptions(t)
	id, _ := subs.subscribe(context.Background(), alice, topicLogs, controlpb.LogLevel_LOG_LEVEL_WARNING)
	engine.log(controlpb.LogLevel_LOG_LEVEL_DEBUG, "debug")
	engine.log(controlpb.LogLevel_LOG_LEVEL_INFO, "info")
	engine.log(controlpb.LogLevel_LOG_LEVEL_WARNING, "warning")
	engine.log(controlpb.LogLevel_LOG_LEVEL_ERROR, "error")
	events, _, _ := subs.poll(alice, id, 0)
	if len(events) != 2 || events[0].GetLog().GetMessage() != "warning" || events[1].GetLog().GetMessage() != "error" {
		t.Fatalf("events = %v", events)
	}
}

func TestUnpolledSubscriptionExpires(t *testing.T) {
	subs, engine, clock := newTestSubscriptions(t)
	id, _ := subs.subscribe(context.Background(), alice, topicLogs, 0)
	clock.advance(subscriptionTTL - time.Second)
	if _, _, err := subs.poll(alice, id, 0); err != nil {
		t.Fatalf("poll before the deadline = %v", err)
	}
	clock.advance(subscriptionTTL - time.Second)
	if _, _, err := subs.poll(alice, id, 0); err != nil {
		t.Fatal("polling did not renew the subscription")
	}
	clock.advance(subscriptionTTL + time.Second)
	if _, _, err := subs.poll(alice, id, 0); err != errUnknownSubscription {
		t.Fatalf("poll after 30 s idle = %v, want errUnknownSubscription", err)
	}
	// The pump also sweeps, and releases mihomo's logger with the last log subscription.
	if _, err := subs.subscribe(context.Background(), alice, topicTraffic, 0); err != nil {
		t.Fatal(err)
	}
	clock.advance(subscriptionTTL + time.Second)
	for deadline := time.Now().Add(5 * time.Second); engine.watchers() != 0 || subs.count() != 0; {
		if time.Now().After(deadline) {
			t.Fatalf("watchers = %d, subscriptions = %d after expiry", engine.watchers(), subs.count())
		}
		time.Sleep(time.Millisecond)
	}
}

func TestSubscriptionEndsWithItsConnection(t *testing.T) {
	subs, _, _ := newTestSubscriptions(t)
	ctx, disconnect := context.WithCancel(context.Background())
	id, _ := subs.subscribe(ctx, alice, topicTraffic, 0)
	disconnect()
	for deadline := time.Now().Add(5 * time.Second); ; {
		if _, _, err := subs.poll(alice, id, 0); err == errUnknownSubscription {
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("the subscription outlived its connection")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestSubscriptionsBelongToTheirOwner(t *testing.T) {
	subs, _, _ := newTestSubscriptions(t)
	id, _ := subs.subscribe(context.Background(), alice, topicTraffic, 0)
	mallory := peer.Identity{UserID: "502"}
	if _, _, err := subs.poll(mallory, id, 0); err != errUnknownSubscription {
		t.Fatalf("another user's poll = %v", err)
	}
	if err := subs.unsubscribe(mallory, id); err != errUnknownSubscription {
		t.Fatalf("another user's unsubscribe = %v", err)
	}
	if err := subs.unsubscribe(alice, id); err != nil {
		t.Fatal(err)
	}
	if err := subs.unsubscribe(alice, id); err != errUnknownSubscription {
		t.Fatalf("second unsubscribe = %v", err)
	}
}

func TestSubscriptionLimit(t *testing.T) {
	subs, _, _ := newTestSubscriptions(t)
	for range maxSubscriptions {
		if _, err := subs.subscribe(context.Background(), alice, topicTraffic, 0); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := subs.subscribe(context.Background(), alice, topicTraffic, 0); err != errTooManySubscriptions {
		t.Fatalf("subscription past the limit = %v", err)
	}
}

func TestCloseStopsTheBackgroundWork(t *testing.T) {
	before := runtime.NumGoroutine()
	engine := &sampler{}
	subs := newSubscriptions(engine)
	subs.period = time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for _, kind := range []topic{topicLogs, topicTraffic, topicConnections} {
		if _, err := subs.subscribe(ctx, alice, kind, 0); err != nil {
			t.Fatal(err)
		}
	}
	subs.close()
	if engine.watchers() != 0 || subs.count() != 0 {
		t.Fatalf("after close: watchers = %d, subscriptions = %d", engine.watchers(), subs.count())
	}
	for deadline := time.Now().Add(5 * time.Second); runtime.NumGoroutine() > before; {
		if time.Now().After(deadline) {
			t.Fatalf("goroutines %d -> %d after close", before, runtime.NumGoroutine())
		}
		time.Sleep(time.Millisecond)
	}
}

// PollEvents over the dispatcher uses the protocol's codes, which ServiceHost relies on.
func TestPollEventsCodes(t *testing.T) {
	dispatcher := New(Options{Engine: &sampler{}, HostMethods: true})
	defer dispatcher.Close()
	subscribe := call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SubscribeTraffic{SubscribeTraffic: &controlpb.SubscribeTrafficRequest{}}
	}))
	id := subscribe.GetSubscribe().GetSubscriptionId()
	if id == 0 {
		t.Fatalf("subscribe = %v", subscribe)
	}
	poll := func(requestID, subscription uint64) *controlpb.Response {
		return call(t, dispatcher, request(requestID, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_PollEvents{PollEvents: &controlpb.PollEventsRequest{SubscriptionId: subscription}}
		}))
	}
	expectOK(t, poll(2, id))
	expectError(t, poll(3, id+1), 3, CodeUnknownSubscription)
	expectOK(t, call(t, dispatcher, request(4, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Unsubscribe{Unsubscribe: &controlpb.UnsubscribeRequest{SubscriptionId: id}}
	})))
	expectError(t, poll(5, id), 5, CodeUnknownSubscription)
}
