package owner

import "context"

type sessionJob struct {
	sequence uint64
	cancel   context.CancelFunc
}

// SessionChanged records the event before scheduling slow work. In particular, sign-out
// cancels a queued sign-in/resume without waiting for it to finish. Jobs for other users
// do not supersede the owner's event, and repeated logons/logoffs of one session are ordered.
func (s *Service) SessionChanged(session uint32, signIn bool) {
	ctx, sequence, ours := s.beginSession(session, signIn)
	go func() {
		defer s.finishSession(session, sequence)
		if signIn {
			s.signedIn(ctx, session, sequence)
		} else {
			s.signedOutOf(ctx, session, ours)
		}
	}()
}

func (s *Service) beginSession(session uint32, signIn bool) (context.Context, uint64, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if previous, ok := s.sessionJobs[session]; ok {
		previous.cancel()
	}
	s.sessionSequence++
	sequence := s.sessionSequence
	ctx, cancel := context.WithCancel(s.ctx)
	s.sessionJobs[session] = sessionJob{sequence: sequence, cancel: cancel}
	ours := s.owner != nil && s.bound && s.owner.Session == session
	if !signIn && ours {
		s.ownerSessionSequence = sequence
		s.signedOut = true
		s.cancelPowerLocked()
	}
	return ctx, sequence, ours
}

func (s *Service) finishSession(session uint32, sequence uint64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if current, ok := s.sessionJobs[session]; ok && current.sequence == sequence {
		current.cancel()
		delete(s.sessionJobs, session)
	}
}
