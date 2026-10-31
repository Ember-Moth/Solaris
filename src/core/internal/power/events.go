// Package power coalesces Windows suspend/resume notifications without doing recovery
// work on an SCM or Powrprof callback. Remembering the cycle as well as the latest state
// means a fast sleep+wake is not lost when the consumer has not read the sleep event yet.
package power

import (
	"net"
	"net/netip"
	"sync"
	"time"
)

const (
	Suspend         = uint32(4) // PBT_APMSUSPEND
	ResumeCritical  = uint32(6)
	ResumeUser      = uint32(7) // PBT_APMRESUMESUSPEND
	ResumeAutomatic = uint32(18)
)

type Event struct {
	Cycle    uint64
	Sleeping bool
}

type Queue struct {
	mu          sync.Mutex
	latest      Event
	initialized bool
	lastResume  time.Time
	now         func() time.Time
	changed     chan struct{}
}

func NewQueue() *Queue { return &Queue{now: time.Now, changed: make(chan struct{}, 1)} }

func (q *Queue) Changed() <-chan struct{} { return q.changed }

func (q *Queue) Latest() Event {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.latest
}

func (q *Queue) Publish(kind uint32) {
	q.mu.Lock()
	defer q.mu.Unlock()
	next := q.latest
	switch kind {
	case Suspend:
		if q.initialized && next.Sleeping {
			return
		}
		next.Cycle++
		next.Sleeping = true
	case ResumeAutomatic, ResumeUser, ResumeCritical:
		if q.initialized && !next.Sleeping {
			// Automatic/user wake are two notifications of one wake, even if user
			// activity comes much later. Critical resume can have no preceding sleep.
			if kind != ResumeCritical || q.now().Sub(q.lastResume) < 2*time.Second {
				return
			}
			next.Cycle++
		} else if !q.initialized {
			next.Cycle++
		}
		next.Sleeping = false
		q.lastResume = q.now()
	default:
		return
	}
	q.initialized = true
	q.latest = next
	select {
	case q.changed <- struct{}{}:
	default:
	}
}

// NetworkAvailable checks local link/address readiness, not internet reachability. It
// sends no probe traffic. Recovery calls it after removing its own TUN, so that TUN's
// address cannot stand in for a working uplink. Start still reports any provider failure.
func NetworkAvailable() bool {
	interfaces, err := net.Interfaces()
	if err != nil {
		return false
	}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, address := range addresses {
			prefix, err := netip.ParsePrefix(address.String())
			if err == nil && prefix.Addr().IsGlobalUnicast() {
				return true
			}
		}
	}
	return false
}
