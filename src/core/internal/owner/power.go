package owner

import (
	"context"
	"errors"
	"fmt"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/power"
)

type powerState struct {
	event      power.Event
	seen       bool
	generation uint64
	cancel     context.CancelFunc
	status     controlpb.RecoveryState
	errorCode  string
}

func (s *Service) cancelPowerLocked() {
	s.power.generation++
	if s.power.cancel != nil {
		s.power.cancel()
		s.power.cancel = nil
	}
	s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_NONE, ""
}

func (s *Service) resumeAllowedLocked() bool {
	return !s.closed && s.ctx.Err() == nil && !s.signedOut && !s.resumeBlocked &&
		!s.power.event.Sleeping && s.wantRunning && s.bound && !s.restarting &&
		s.owner != nil && s.profile != nil && len(s.platform.SignedIn(s.owner.SID)) > 0
}

// resume records an automatic attempt before entering the core. If the process dies
// there, a replacement service stays up but leaves the tunnel down for an explicit retry.
func (s *Service) resume(ctx context.Context, profile *controlpb.StartRequest) error {
	s.mu.Lock()
	if !s.resumeAllowedLocked() {
		s.mu.Unlock()
		return nil
	}
	if err := s.home.markResumeAttempt(); err != nil {
		s.mu.Unlock()
		return fmt.Errorf("record automatic resume attempt: %w", err)
	}
	s.resumeToken++
	token := s.resumeToken
	s.mu.Unlock()
	err := s.core.ResumeContext(ctx, profile)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.resumeToken == token {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, operation.ErrUnresponsive) {
			s.resumeBlocked = true
			if s.wantRunning {
				s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_FAILED, "core_unresponsive"
			}
		} else if clearErr := s.home.clearResumeAttempt(); clearErr != nil {
			s.log.Warn("clear automatic resume guard", "error", clearErr)
		}
	}
	return err
}

func (s *Service) RecoveryStatus() (controlpb.RecoveryState, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.power.status, s.power.errorCode
}

// PowerChanged schedules work and returns. Native/SCM callbacks only update the queue;
// this method does not wait for network readiness or a library operation either.
func (s *Service) PowerChanged(event power.Event) {
	s.mu.Lock()
	if s.closed || s.ctx.Err() != nil || (s.power.seen && s.power.event == event) {
		s.mu.Unlock()
		return
	}
	s.cancelPowerLocked()
	s.power.event, s.power.seen = event, true
	if !s.bound || s.owner == nil || s.restarting || s.core == nil {
		s.mu.Unlock()
		return
	}
	if s.resumeBlocked && s.wantRunning {
		s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_FAILED, "core_unresponsive"
		s.mu.Unlock()
		return
	}
	if !event.Sleeping && !s.resumeAllowedLocked() {
		s.mu.Unlock()
		return
	}
	ctx, cancel := context.WithTimeout(s.ctx, time.Minute)
	s.power.cancel = cancel
	generation, sid := s.power.generation, s.owner.SID
	if event.Sleeping {
		if s.wantRunning {
			s.power.status = controlpb.RecoveryState_RECOVERY_STATE_SLEEPING
		}
	} else {
		s.power.status = controlpb.RecoveryState_RECOVERY_STATE_WAITING_NETWORK
	}
	delays := append([]time.Duration(nil), s.powerDelays...)
	s.mu.Unlock()
	go func() {
		defer func() {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				s.finishPower(generation, "recovery_timeout")
			}
			cancel()
		}()
		if event.Sleeping {
			if !s.powerCurrent(ctx, generation, sid, false) {
				return
			}
			if err := s.core.SuspendContext(ctx); err != nil && ctx.Err() == nil {
				s.log.Error("suspend the tunnel for sleep", "error", err)
				s.finishPower(generation, "suspend_failed")
			}
			return
		}
		s.recoverPower(ctx, generation, sid, delays)
	}()
}

func (s *Service) powerCurrent(ctx context.Context, generation uint64, sid string, resume bool) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return ctx.Err() == nil && !s.closed && s.owner != nil && s.owner.SID == sid &&
		s.power.generation == generation && !s.restarting && (!resume || s.resumeAllowedLocked())
}

func (s *Service) powerStatus(generation uint64, status controlpb.RecoveryState, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.power.generation == generation {
		s.power.status, s.power.errorCode = status, reason
	}
}

func (s *Service) finishPower(generation uint64, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.power.generation != generation {
		return
	}
	if s.power.cancel != nil {
		s.power.cancel()
		s.power.cancel = nil
	}
	if reason == "" {
		s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_NONE, ""
	} else {
		s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_FAILED, reason
	}
}

func (s *Service) recoverPower(ctx context.Context, generation uint64, sid string, delays []time.Duration) {
	if !s.powerCurrent(ctx, generation, sid, true) {
		return
	}
	// Reset stale sockets and the old TUN even when a critical wake had no sleep notice.
	if err := s.core.SuspendContext(ctx); err != nil {
		if ctx.Err() == nil {
			s.finishPower(generation, "suspend_failed")
		}
		return
	}
	reason := "network_unavailable"
	for _, delay := range delays {
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				s.finishPower(generation, "recovery_timeout")
			}
			return
		case <-timer.C:
		}
		if !s.powerCurrent(ctx, generation, sid, true) {
			return
		}
		if !s.platform.NetworkReady() {
			s.powerStatus(generation, controlpb.RecoveryState_RECOVERY_STATE_WAITING_NETWORK, "")
			reason = "network_unavailable"
			continue
		}
		s.mu.Lock()
		profile := proto.Clone(s.profile).(*controlpb.StartRequest)
		s.mu.Unlock()
		s.powerStatus(generation, controlpb.RecoveryState_RECOVERY_STATE_CONNECTING, "")
		err := s.resume(ctx, profile)
		if !s.powerCurrent(ctx, generation, sid, true) {
			return
		}
		if err == nil && s.running() {
			s.finishPower(generation, "")
			return
		}
		if err != nil {
			s.log.Warn("restore the tunnel after wake", "error", err)
		}
		reason = "resume_failed"
		if errors.Is(err, operation.ErrUnresponsive) || errors.Is(err, context.DeadlineExceeded) {
			s.finishPower(generation, "core_unresponsive")
			return
		}
		if errors.Is(err, engine.ErrInvalidConfig) || errors.Is(err, engine.ErrUnsupported) || errors.Is(err, engine.ErrInvalidArgument) {
			break
		}
		if err != nil && s.running() {
			// The core may be up although applying the owner's system proxy failed.
			// Resume leaves an already-running core alone, so reset before retrying.
			if stopErr := s.core.SuspendContext(ctx); stopErr != nil {
				s.finishPower(generation, "suspend_failed")
				return
			}
		}
	}
	s.finishPower(generation, reason)
}
