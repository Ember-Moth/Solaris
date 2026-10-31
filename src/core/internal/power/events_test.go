package power

import (
	"testing"
	"time"
)

func TestNotificationsOfOneWakeAreCoalesced(t *testing.T) {
	q := NewQueue()
	q.Publish(Suspend)
	q.Publish(Suspend) // SCM and Powrprof can both report it
	q.Publish(ResumeAutomatic)
	q.Publish(ResumeUser)
	if event := q.Latest(); event.Cycle != 1 || event.Sleeping {
		t.Fatalf("event=%+v", event)
	}
	<-q.Changed()
	q.Publish(ResumeUser)
	select {
	case <-q.Changed():
		t.Fatal("same wake restarted recovery")
	default:
	}
	q.Publish(Suspend)
	q.Publish(ResumeAutomatic)
	if event := q.Latest(); event.Cycle != 2 || event.Sleeping {
		t.Fatalf("event=%+v", event)
	}
}

func TestCriticalResumeWithoutSleepStartsANewCycle(t *testing.T) {
	q := NewQueue()
	now := time.Now()
	q.now = func() time.Time { return now }
	q.Publish(ResumeCritical)
	q.Publish(ResumeAutomatic)
	q.Publish(ResumeCritical)
	if q.Latest().Cycle != 1 {
		t.Fatal(q.Latest())
	}
	now = now.Add(time.Minute)
	q.Publish(ResumeCritical)
	if q.Latest().Cycle != 2 {
		t.Fatal(q.Latest())
	}
}

func TestFastTransitionsKeepTheLatestCycleWhileConsumerIsBusy(t *testing.T) {
	q := NewQueue()
	for range 1000 {
		q.Publish(Suspend)
		q.Publish(ResumeAutomatic)
	}
	<-q.Changed()
	if got := q.Latest(); got.Cycle != 1000 || got.Sleeping {
		t.Fatal(got)
	}
	q.Publish(10) // battery/AC changes are not sleep
	select {
	case <-q.Changed():
		t.Fatal("ordinary power change triggered recovery")
	default:
	}
}
