package owner

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"slices"
	"sync"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/sysproxy"
)

// Platform is what the service needs from Windows; tests stand in for it.
type Platform interface {
	// SecureDir creates dir if needed so that SYSTEM alone writes it. reader, a SID, may read
	// it; with no reader Administrators get full access instead. It refuses a link, and a
	// directory that someone other than SYSTEM or Administrators created.
	SecureDir(dir, reader string) error
	// SessionUser is the SID of the user signed in to session.
	SessionUser(session uint32) (string, error)
	// SignedIn lists the sessions sid is signed in to.
	SignedIn(sid string) []uint32
	ReadProxy(session uint32) (sysproxy.Settings, error)
	WriteProxy(session uint32, settings sysproxy.Settings) error
	// Restart replaces this service process with a new one once the current request is
	// answered.
	Restart()
	NetworkReady() bool
}

// Core is the dispatcher's side of the tunnel. ResumeContext and SuspendContext give up on
// their context only while they wait for their turn: once in the core, they return how
// they ended.
type Core interface {
	Running() bool
	ResumeContext(context.Context, *controlpb.StartRequest) error
	SuspendContext(context.Context) error
}

// Service implements control.Host for the Windows service.
type Service struct {
	root     string
	platform Platform
	log      *slog.Logger
	core     Core
	ctx      context.Context
	cancel   context.CancelFunc

	// proxyMu orders changes to the owner's system proxy, which go through the OS as the
	// owner and can take a while, and guards proxy.json. keepMu orders the writes of what
	// the next sign-in needs. Either comes before mu, which only guards the state below:
	// it is never held across the OS proxy or the core, so admission and a Stop's intent
	// wait for nothing slower than a small write.
	proxyMu sync.Mutex
	keepMu  sync.Mutex

	mu                   sync.Mutex
	closed               bool
	signedOut            bool
	resumeBlocked        bool
	resumeToken          uint64
	power                powerState
	powerDelays          []time.Duration
	sessionJobs          map[uint32]sessionJob
	sessionSequence      uint64
	ownerSessionSequence uint64
	// owner is nil until the first claim. bound says this process opened the engine in the
	// owner's home; a process started without an owner never does.
	owner      *Record
	bound      bool
	home       kept
	restarting bool
	// profile is the one the core runs, or ran last.
	profile     *controlpb.StartRequest
	wantRunning bool
	// proxyPort is the mixed port the owner's system proxy points at; 0 while it is theirs.
	// It changes under both proxyMu and mu.
	proxyPort uint16
}

var _ control.Host = (*Service)(nil)

// Open secures the service's root and readies its owner's home, which it returns for the
// engine: the home gets the GEO data in geodataDir, the files the installer ships, unless
// it has its own. With no owner yet the home is "", and the process serves GetInfo and
// Claim only.
func Open(ctx context.Context, root, geodataDir string, platform Platform, log *slog.Logger) (*Service, string, error) {
	for _, dir := range []string{root, filepath.Join(root, usersDir)} {
		if err := platform.SecureDir(dir, ""); err != nil {
			return nil, "", err
		}
	}
	ctx, cancel := context.WithCancel(ctx)
	opened := false
	defer func() {
		if !opened {
			cancel()
		}
	}()
	s := &Service{root: root, platform: platform, log: log, ctx: ctx, cancel: cancel,
		powerDelays: []time.Duration{0, time.Second, 2 * time.Second, 5 * time.Second, 10 * time.Second},
		sessionJobs: make(map[uint32]sessionJob)}
	record, ok, err := LoadRecord(root)
	if err != nil {
		// The next claim writes a new record.
		log.Warn("ignored the owner record", "error", err)
	}
	if !ok {
		opened = true
		return s, "", nil
	}
	home := Home(root, record.SID)
	if err := platform.SecureDir(home, record.SID); err != nil {
		return nil, "", err
	}
	// Keep the service available for diagnostics if seeding fails. The embedded core
	// rejects profiles needing missing GEO data; it does not download during admission.
	if err := geodata.Seed(geodataDir, home); err != nil {
		log.Warn("seed the GEO data", "error", err)
	}
	s.owner, s.bound, s.home = &record, true, kept{home}
	s.wantRunning = s.home.wasRunning()
	s.resumeBlocked = s.home.resumeInterrupted()
	if s.resumeBlocked && s.wantRunning {
		s.power.status, s.power.errorCode = controlpb.RecoveryState_RECOVERY_STATE_FAILED, "core_unresponsive"
	}
	if s.profile, err = s.home.profile(); err != nil {
		log.Warn("ignored the kept profile", "error", err)
	}
	opened = true
	return s, home, nil
}

// Attach hands the service the core, then brings the owner's tunnel back if it was up and
// they are signed in; otherwise their next sign-in does (§5.2).
func (s *Service) Attach(core Core) {
	s.core = core
	s.mu.Lock()
	owner := s.owner
	s.mu.Unlock()
	if owner == nil {
		return
	}
	sessions := s.platform.SignedIn(owner.SID)
	if len(sessions) == 0 {
		return
	}
	session := sessions[0]
	if slices.Contains(sessions, owner.Session) {
		session = owner.Session
	}
	s.SignedIn(session)
}

func (s *Service) running() bool { return s.core != nil && s.core.Running() }

func (s *Service) Ownership(caller peer.Identity) controlpb.Ownership {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case s.owner != nil && s.owner.SID == caller.UserID:
		return controlpb.Ownership_OWNERSHIP_CALLER
	case s.owner != nil && s.running():
		return controlpb.Ownership_OWNERSHIP_OTHER
	}
	return controlpb.Ownership_OWNERSHIP_AVAILABLE
}

func (s *Service) Admit(caller peer.Identity) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.admitLocked(caller)
}

func (s *Service) admitLocked(caller peer.Identity) error {
	switch {
	case s.closed:
		return control.ErrRestarting
	case s.restarting:
		return control.ErrRestarting
	case s.owner == nil:
		return control.ErrNotClaimed
	case s.owner.SID != caller.UserID && s.running():
		return control.ErrOwnedByOtherUser
	case s.owner.SID != caller.UserID:
		return control.ErrNotClaimed
	case !s.bound:
		return control.ErrRestarting
	}
	return nil
}

func (s *Service) StopRequested(caller peer.Identity, supersede func()) error {
	s.mu.Lock()
	if err := s.admitLocked(caller); err != nil {
		s.mu.Unlock()
		return err
	}
	supersede()
	s.wantRunning = false
	s.cancelPowerLocked()
	s.mu.Unlock()
	return s.keepRunning()
}

func (s *Service) ResumeAllowed() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.resumeAllowedLocked()
}

// Claim takes on caller when the service has no owner, is theirs, or the owner's tunnel is
// down. Whether that tunnel would come back at the owner's next sign-in does not hold the
// service: the owner may never sign in again.
func (s *Service) Claim(caller peer.Identity) error {
	if !ValidSID(caller.UserID) {
		return fmt.Errorf("caller %q has no SID", caller.UserID)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.claimLocked(caller)
}

// TakeOver is Claim for an administrator, who may also take over another user's running
// tunnel once they confirmed it through UAC (decision D5b). stop brings that tunnel down; it
// stays down as after Stop, and its owner gets their proxy settings back now, or at their
// next sign-in when the service cannot write them. Then the service restarts for caller, as
// for any new owner. Anyone else waits for the tunnel, as with Claim.
func (s *Service) TakeOver(caller peer.Identity, stop func() error) error {
	if !ValidSID(caller.UserID) {
		return fmt.Errorf("caller %q has no SID", caller.UserID)
	}
	s.mu.Lock()
	err := s.claimLocked(caller)
	former := ""
	if s.owner != nil {
		former = s.owner.SID
	}
	s.mu.Unlock()
	if !errors.Is(err, control.ErrOwnedByOtherUser) {
		return err
	}
	if !caller.Administrator {
		return fmt.Errorf("taking over needs an administrator's rights: %w", err)
	}
	// The core stops, and the former owner's proxy settings come back, outside mu. The
	// dispatcher's operation lock keeps any other claim or start out meanwhile.
	if err := stop(); err != nil {
		return fmt.Errorf("stop the other user's tunnel: %w", err)
	}
	if err := s.Stopped(); err != nil {
		s.log.Warn("restore the former owner's proxy settings", "error", err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log.Info("an administrator took over the tunnel", "from", former, "sid", caller.UserID)
	return s.claimLocked(caller)
}

func (s *Service) claimLocked(caller peer.Identity) error {
	switch {
	case s.restarting:
		return control.ErrRestarting
	case s.owner != nil && s.owner.SID == caller.UserID:
		s.signedOut = false
		if s.owner.Session != caller.SessionID {
			s.owner.Session = caller.SessionID
			s.saveRecordLocked()
		}
		return nil
	case s.owner != nil && s.running():
		return control.ErrOwnedByOtherUser
	}
	record := Record{SID: caller.UserID, Session: caller.SessionID}
	if err := SaveRecord(s.root, record); err != nil {
		return fmt.Errorf("record the owner: %w", err)
	}
	s.log.Info("new owner; restarting to open their home", "sid", record.SID)
	s.cancelPowerLocked()
	s.owner, s.restarting = &record, true
	s.platform.Restart()
	return control.ErrRestarting
}

func (s *Service) Applied(start *controlpb.StartRequest, current func() bool) error {
	s.mu.Lock()
	if s.closed || s.ctx.Err() != nil || !current() {
		s.mu.Unlock()
		return context.Canceled
	}
	s.profile = start
	s.wantRunning = true
	s.mu.Unlock()
	s.keep()
	var applied error
	if options := start.GetOptions(); options.GetSystemProxyEnabled() {
		applied = s.proxyOn(uint16(options.GetMixedPort()))
	} else {
		applied = s.proxyOff()
	}
	if applied != nil {
		return applied
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	// Keep the crash-loop guard until persistence and the OS proxy operation have
	// both returned. Either can stall after the core itself has already started.
	s.resumeBlocked = false
	s.resumeToken++
	if err := s.home.clearResumeAttempt(); err != nil {
		s.log.Warn("clear automatic resume guard", "error", err)
	}
	if !s.power.event.Sleeping {
		s.cancelPowerLocked()
	}
	return nil
}

// Stopped follows the tunnel going down until someone starts it again: it does not come
// back at the owner's next sign-in.
func (s *Service) Stopped() error {
	s.mu.Lock()
	s.wantRunning = false
	s.cancelPowerLocked()
	s.mu.Unlock()
	if err := s.keepRunning(); err != nil {
		s.log.Error("keep was_running", "error", err)
	}
	return s.proxyOff()
}

func (s *Service) Suspended() error {
	return s.proxyOff()
}

// Suspending captures a live mode change in the service's runtime copy, not in the
// subscription. Stop clears mihomo's runtime mode, so a wake must restore it explicitly.
func (s *Service) Suspending(mode controlpb.Mode) {
	name := map[controlpb.Mode]string{
		controlpb.Mode_MODE_RULE: "rule", controlpb.Mode_MODE_GLOBAL: "global", controlpb.Mode_MODE_DIRECT: "direct",
	}[mode]
	if name == "" {
		return
	}
	s.mu.Lock()
	if s.closed || !s.wantRunning || s.profile == nil {
		s.mu.Unlock()
		return
	}
	const prefix = "# Solaris automatic recovery mode\nmode: "
	profile := proto.Clone(s.profile).(*controlpb.StartRequest)
	profile.Overrides = slices.DeleteFunc(profile.Overrides, func(value string) bool {
		return value == prefix+"rule\n" || value == prefix+"global\n" || value == prefix+"direct\n"
	})
	profile.Overrides = append(profile.Overrides, prefix+name+"\n")
	s.profile = profile
	s.mu.Unlock()
	if err := s.keepProfile(); err != nil {
		s.log.Warn("keep mode for automatic recovery", "error", err)
	}
}

// SetSystemProxy turns the owner's system proxy on or off while the core runs; the choice
// is kept with the profile for the next sign-in.
func (s *Service) SetSystemProxy(enabled bool) error {
	if !enabled {
		if err := s.proxyOff(); err != nil {
			return err
		}
	} else {
		s.mu.Lock()
		profile := s.profile
		s.mu.Unlock()
		if !s.running() || profile == nil {
			return engine.ErrNotRunning
		}
		if err := s.proxyOn(uint16(profile.GetOptions().GetMixedPort())); err != nil {
			return err
		}
	}
	s.mu.Lock()
	changed := s.profile != nil && s.profile.GetOptions().GetSystemProxyEnabled() != enabled
	if changed {
		profile := proto.CloneOf(s.profile)
		if profile.Options == nil {
			profile.Options = &controlpb.RunOptions{}
		}
		profile.Options.SystemProxyEnabled = enabled
		s.profile = profile
	}
	s.mu.Unlock()
	// Only the profile changes: whether the tunnel comes back stays the owner's intent.
	if changed {
		if err := s.keepProfile(); err != nil {
			s.log.Error("keep the profile", "error", err)
		}
	}
	return nil
}

func (s *Service) SystemProxyEnabled() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.proxyPort != 0
}

// Close gives the owner their proxy settings back as the service stops, for a shutdown or an
// upgrade. was_running stays, so the next start brings the tunnel and the proxy back.
func (s *Service) Close() {
	s.cancel()
	s.mu.Lock()
	s.closed = true
	s.cancelPowerLocked()
	bound := s.bound
	s.mu.Unlock()
	if !bound {
		return
	}
	if err := s.proxyOff(); err != nil {
		s.log.Warn("restore the owner's proxy settings", "error", err)
	}
}

// SignedIn follows a sign-in to session. The owner gets their tunnel back if it was up;
// a user whose proxy settings the service left behind gets them back.
func (s *Service) SignedIn(session uint32) {
	ctx, sequence, _ := s.beginSession(session, true)
	defer s.finishSession(session, sequence)
	s.signedIn(ctx, session, sequence)
}

func (s *Service) signedIn(ctx context.Context, session uint32, sequence uint64) {
	if ctx.Err() != nil {
		return
	}
	sid, err := s.platform.SessionUser(session)
	if err != nil {
		s.log.Warn("read the user of a new session", "session", session, "error", err)
		return
	}
	s.mu.Lock()
	if ctx.Err() != nil || s.closed || (s.owner != nil && s.owner.SID == sid && sequence < s.ownerSessionSequence) {
		s.mu.Unlock()
		return
	}
	if s.owner == nil || s.owner.SID != sid || !s.bound || s.restarting {
		s.mu.Unlock()
		s.restoreStrayProxy(sid, session)
		return
	}
	if s.owner.Session != session {
		s.owner.Session = session
		s.saveRecordLocked()
	}
	s.ownerSessionSequence = sequence
	s.signedOut = false
	profile := s.profile
	resume := profile != nil && s.resumeAllowedLocked() && !s.running()
	s.mu.Unlock()
	if !resume {
		if err := s.restoreLeftProxy(); err != nil {
			s.log.Warn("restore the owner's proxy settings", "error", err)
		}
		return
	}
	if err := s.resume(ctx, profile); err != nil {
		s.log.Error("bring the owner's tunnel back", "error", err)
		return
	}
	if s.running() {
		s.log.Info("brought the owner's tunnel back", "session", session)
	}
}

// SignedOut stops the tunnel when its owner signs out of the session they use it from: the
// TUN takes every user's traffic (decision D5). It comes back at their next sign-in.
func (s *Service) SignedOut(session uint32) {
	ctx, sequence, ours := s.beginSession(session, false)
	defer s.finishSession(session, sequence)
	s.signedOutOf(ctx, session, ours)
}

func (s *Service) signedOutOf(ctx context.Context, session uint32, ours bool) {
	if !ours {
		return
	}
	if err := s.core.SuspendContext(ctx); err != nil {
		s.log.Error("stop the tunnel at the owner's sign-out", "error", err)
		return
	}
	s.log.Info("stopped the tunnel at the owner's sign-out", "session", session)
}

// keep writes what the next sign-in needs. A failure only costs that, so it is logged
// rather than failing the request that already took effect.
func (s *Service) keep() {
	if err := s.keepProfile(); err != nil {
		s.log.Error("keep the profile", "error", err)
	}
	if err := s.keepRunning(); err != nil {
		s.log.Error("keep was_running", "error", err)
	}
}

// keepProfile writes the profile the core runs. Like keepRunning, it writes the service's
// state as it is once its turn comes, not as its caller saw it, so writes that finish in
// any order leave the latest.
func (s *Service) keepProfile() error {
	s.keepMu.Lock()
	defer s.keepMu.Unlock()
	s.mu.Lock()
	profile := s.profile
	s.mu.Unlock()
	if profile == nil {
		return nil
	}
	return s.home.saveProfile(profile)
}

// keepRunning writes was_running, the owner's intent: a Stop's write is never overtaken by
// that of the Start it superseded.
func (s *Service) keepRunning() error {
	s.keepMu.Lock()
	defer s.keepMu.Unlock()
	s.mu.Lock()
	running := s.wantRunning
	s.mu.Unlock()
	return s.home.setWasRunning(running)
}

func (s *Service) saveRecordLocked() {
	if err := SaveRecord(s.root, *s.owner); err != nil {
		s.log.Error("record the owner's session", "error", err)
	}
}

// sessionLocked is a session the owner is signed in to: the one they last used if they still
// are, or else another.
func (s *Service) sessionLocked() (uint32, error) {
	if user, err := s.platform.SessionUser(s.owner.Session); err == nil && user == s.owner.SID {
		return s.owner.Session, nil
	}
	sessions := s.platform.SignedIn(s.owner.SID)
	if len(sessions) == 0 {
		return 0, errors.New("the owner is not signed in")
	}
	s.owner.Session = sessions[0]
	s.saveRecordLocked()
	return sessions[0], nil
}

// proxyOn points the owner's system proxy at the mixed port, first keeping their own
// settings to restore. A closed service has given them back and sets nothing more.
func (s *Service) proxyOn(port uint16) error {
	if port == 0 {
		return errors.New("the system proxy needs a mixed port")
	}
	s.proxyMu.Lock()
	defer s.proxyMu.Unlock()
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return context.Canceled
	}
	if s.proxyPort == port {
		s.mu.Unlock()
		return nil
	}
	session, err := s.sessionLocked()
	s.mu.Unlock()
	if err != nil {
		return err
	}
	if _, saved, err := s.home.previousProxy(); err != nil {
		return err
	} else if !saved {
		previous, err := s.platform.ReadProxy(session)
		if err != nil {
			return fmt.Errorf("read the owner's proxy settings: %w", err)
		}
		if err := s.home.savePreviousProxy(previous); err != nil {
			return err
		}
	}
	if err := s.platform.WriteProxy(session, sysproxy.Core(port)); err != nil {
		return fmt.Errorf("set the owner's system proxy: %w", err)
	}
	s.mu.Lock()
	s.proxyPort = port
	s.mu.Unlock()
	return nil
}

// proxyOff gives the owner their own settings back. When they are not signed in, the
// settings wait in their home for the next sign-in.
func (s *Service) proxyOff() error {
	s.proxyMu.Lock()
	defer s.proxyMu.Unlock()
	return s.proxyOffLocked()
}

// restoreLeftProxy gives the owner back settings the service left behind, by a crash say,
// unless the service's own are in place.
func (s *Service) restoreLeftProxy() error {
	s.proxyMu.Lock()
	defer s.proxyMu.Unlock()
	s.mu.Lock()
	inPlace := s.proxyPort != 0
	s.mu.Unlock()
	if inPlace {
		return nil
	}
	return s.proxyOffLocked()
}

// proxyOffLocked is proxyOff under proxyMu.
func (s *Service) proxyOffLocked() error {
	s.mu.Lock()
	s.proxyPort = 0
	s.mu.Unlock()
	previous, saved, err := s.home.previousProxy()
	if err != nil || !saved {
		return err
	}
	s.mu.Lock()
	session, err := s.sessionLocked()
	s.mu.Unlock()
	if err != nil {
		return err
	}
	if err := s.platform.WriteProxy(session, previous); err != nil {
		return fmt.Errorf("restore the owner's proxy settings: %w", err)
	}
	return s.home.dropPreviousProxy()
}

// restoreStrayProxy gives back the settings of a former owner, which the service could not
// restore before someone else claimed it.
func (s *Service) restoreStrayProxy(sid string, session uint32) {
	if !ValidSID(sid) {
		return
	}
	home := kept{Home(s.root, sid)}
	previous, saved, err := home.previousProxy()
	if err != nil || !saved {
		return
	}
	if err := s.platform.WriteProxy(session, previous); err != nil {
		s.log.Warn("restore a former owner's proxy settings", "error", err)
		return
	}
	if err := home.dropPreviousProxy(); err != nil {
		s.log.Warn("forget a former owner's proxy settings", "error", err)
	}
}
