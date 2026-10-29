package control

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"sync"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
)

// Subscriptions are pulled, not pushed (docs/architecture.md §4.4): each one buffers its
// events until PollEvents takes them, drops the oldest when full and expires when nobody
// polls it for subscriptionTTL.
const (
	subscriptionTTL  = 30 * time.Second
	maxSubscriptions = 64
	samplePeriod     = time.Second
	// maxPollBytes keeps a PollEvents response well under the 8 MiB frame limit; events
	// past it wait for the next poll.
	maxPollBytes = 4 << 20
)

type topic int

const (
	topicLogs topic = iota
	topicTraffic
	topicConnections
)

// capacity bounds each buffer: about ten minutes of traffic samples, and a few
// connection snapshots because every snapshot supersedes the previous one.
var capacity = map[topic]int{topicLogs: 1024, topicTraffic: 600, topicConnections: 4}

var (
	errUnknownSubscription  = errors.New("unknown or expired subscription")
	errTooManySubscriptions = errors.New("too many subscriptions")
)

type ring struct {
	items      []*controlpb.Event
	head, size int
}

// push appends an event and reports whether the oldest one had to go.
func (r *ring) push(event *controlpb.Event) bool {
	if r.size == len(r.items) {
		r.items[r.head] = event
		r.head = (r.head + 1) % len(r.items)
		return true
	}
	r.items[(r.head+r.size)%len(r.items)] = event
	r.size++
	return false
}

func (r *ring) peek() *controlpb.Event { return r.items[r.head] }

func (r *ring) pop() {
	r.items[r.head] = nil
	r.head = (r.head + 1) % len(r.items)
	r.size--
}

type subscription struct {
	owner    string
	topic    topic
	minLevel controlpb.LogLevel
	buffer   ring
	dropped  uint64
	lastPoll time.Time
	// detach stops the subscription from ending with its connection.
	detach func() bool
}

func (s *subscription) push(event *controlpb.Event) {
	if s.buffer.push(event) {
		s.dropped++
	}
}

type subscriptions struct {
	engine Engine
	now    func() time.Time
	period time.Duration

	mu       sync.Mutex
	byID     map[uint64]*subscription
	pumping  bool
	stopPump chan struct{}
	pumpDone chan struct{}
	stopLogs func()
}

func newSubscriptions(engine Engine) *subscriptions {
	return &subscriptions{engine: engine, now: time.Now, period: samplePeriod, byID: map[uint64]*subscription{}}
}

// subscribe starts a subscription owned by caller. It also ends when ctx does, which
// on the Windows pipe is when the connection closes.
func (s *subscriptions) subscribe(ctx context.Context, caller peer.Identity, kind topic, level controlpb.LogLevel) (uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked()
	if len(s.byID) >= maxSubscriptions {
		return 0, errTooManySubscriptions
	}
	id := s.newIDLocked()
	sub := &subscription{
		owner:    caller.UserID,
		topic:    kind,
		minLevel: level,
		buffer:   ring{items: make([]*controlpb.Event, capacity[kind])},
		lastPoll: s.now(),
	}
	sub.detach = context.AfterFunc(ctx, func() { s.remove(id) })
	s.byID[id] = sub
	if kind == topicLogs && s.stopLogs == nil {
		s.stopLogs = s.engine.WatchLogs(s.onLog)
	}
	if !s.pumping {
		s.pumping = true
		s.stopPump = make(chan struct{})
		s.pumpDone = make(chan struct{})
		go s.pump(s.stopPump, s.pumpDone)
	}
	return id, nil
}

func (s *subscriptions) newIDLocked() uint64 {
	for {
		var raw [8]byte
		_, _ = rand.Read(raw[:])
		// Keep ids within 53 bits so they survive any JSON tooling unchanged.
		id := binary.LittleEndian.Uint64(raw[:]) & (1<<53 - 1)
		if _, taken := s.byID[id]; id != 0 && !taken {
			return id
		}
	}
}

func (s *subscriptions) poll(caller peer.Identity, id uint64, maxEvents uint32) ([]*controlpb.Event, uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked()
	sub, ok := s.byID[id]
	if !ok || sub.owner != caller.UserID {
		return nil, 0, errUnknownSubscription
	}
	sub.lastPoll = s.now()
	count := sub.buffer.size
	if maxEvents != 0 {
		count = min(count, int(maxEvents))
	}
	events := make([]*controlpb.Event, 0, count)
	size := 0
	for len(events) < count {
		event := sub.buffer.peek()
		size += proto.Size(event)
		if len(events) > 0 && size > maxPollBytes {
			break
		}
		events = append(events, event)
		sub.buffer.pop()
	}
	dropped := sub.dropped
	sub.dropped = 0
	return events, dropped, nil
}

func (s *subscriptions) unsubscribe(caller peer.Identity, id uint64) error {
	s.mu.Lock()
	sub, ok := s.byID[id]
	if !ok || sub.owner != caller.UserID {
		s.mu.Unlock()
		return errUnknownSubscription
	}
	s.mu.Unlock()
	s.remove(id)
	return nil
}

func (s *subscriptions) remove(id uint64) {
	s.mu.Lock()
	if sub, ok := s.byID[id]; ok {
		sub.detach()
		delete(s.byID, id)
	}
	stop := s.releaseLogsLocked()
	s.mu.Unlock()
	stop()
}

// sweepLocked drops subscriptions nobody polled within subscriptionTTL.
func (s *subscriptions) sweepLocked() {
	deadline := s.now().Add(-subscriptionTTL)
	for id, sub := range s.byID {
		if sub.lastPoll.Before(deadline) {
			sub.detach()
			delete(s.byID, id)
		}
	}
}

// releaseLogsLocked detaches from mihomo's logger once no log subscription is left. The
// returned function does the detaching and must run without s.mu, because the log
// goroutine it waits for may be waiting for s.mu itself.
func (s *subscriptions) releaseLogsLocked() func() {
	if s.stopLogs == nil {
		return func() {}
	}
	for _, sub := range s.byID {
		if sub.topic == topicLogs {
			return func() {}
		}
	}
	stop := s.stopLogs
	s.stopLogs = nil
	return stop
}

func (s *subscriptions) onLog(entry *controlpb.LogEntry) {
	event := &controlpb.Event{Payload: &controlpb.Event_Log{Log: entry}}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, sub := range s.byID {
		if sub.topic == topicLogs && entry.Level >= sub.minLevel {
			sub.push(event)
		}
	}
}

// pump samples traffic and connections once a period for the subscriptions that want
// them, and expires idle subscriptions. It exits when none are left.
func (s *subscriptions) pump(stop <-chan struct{}, done chan<- struct{}) {
	defer close(done)
	ticker := time.NewTicker(s.period)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
		}
		s.mu.Lock()
		s.sweepLocked()
		release := s.releaseLogsLocked()
		if len(s.byID) == 0 {
			s.pumping = false
			s.mu.Unlock()
			release()
			return
		}
		wanted := map[topic]bool{}
		for _, sub := range s.byID {
			wanted[sub.topic] = true
		}
		s.mu.Unlock()
		release()

		// Every subscriber shares one sample; nothing mutates an event once published.
		samples := map[topic]*controlpb.Event{}
		if wanted[topicTraffic] {
			samples[topicTraffic] = &controlpb.Event{Payload: &controlpb.Event_Traffic{Traffic: s.engine.Traffic()}}
		}
		if wanted[topicConnections] {
			samples[topicConnections] = &controlpb.Event{Payload: &controlpb.Event_Connections{Connections: s.engine.Connections()}}
		}
		s.mu.Lock()
		for _, sub := range s.byID {
			if event, ok := samples[sub.topic]; ok {
				sub.push(event)
			}
		}
		s.mu.Unlock()
	}
}

// close ends every subscription and waits for the background work to stop.
func (s *subscriptions) close() {
	s.mu.Lock()
	for id, sub := range s.byID {
		sub.detach()
		delete(s.byID, id)
	}
	stopLogs := s.releaseLogsLocked()
	var stopPump chan struct{}
	var pumpDone chan struct{}
	if s.pumping {
		s.pumping = false
		stopPump, pumpDone = s.stopPump, s.pumpDone
	}
	s.mu.Unlock()
	stopLogs()
	if stopPump != nil {
		close(stopPump)
		<-pumpDone
	}
}

func (s *subscriptions) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.byID)
}
