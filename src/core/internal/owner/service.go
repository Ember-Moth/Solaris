package owner

import (
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"slices"
	"sync"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
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
}

// Core is the dispatcher's side of the tunnel.
type Core interface {
	Running() bool
	Resume(start *controlpb.StartRequest) error
	Suspend() error
}

// Service implements control.Host for the Windows service.
type Service struct {
	root     string
	platform Platform
	log      *slog.Logger
	core     Core

	mu sync.Mutex
	// owner is nil until the first claim. bound says this process opened the engine in the
	// owner's home; a process started without an owner never does.
	owner      *Record
	bound      bool
	home       kept
	restarting bool
	// profile is the one the core runs, or ran last.
	profile *controlpb.StartRequest
	// proxyPort is the mixed port the owner's system proxy points at; 0 while it is theirs.
	proxyPort uint16
}

var _ control.Host = (*Service)(nil)

// Open secures the service's root and readies its owner's home, which it returns for the
// engine. With no owner yet the home is "", and the process serves GetInfo and Claim only.
func Open(root string, platform Platform, log *slog.Logger) (*Service, string, error) {
	for _, dir := range []string{root, filepath.Join(root, usersDir)} {
		if err := platform.SecureDir(dir, ""); err != nil {
			return nil, "", err
		}
	}
	s := &Service{root: root, platform: platform, log: log}
	record, ok, err := LoadRecord(root)
	if err != nil {
		// The next claim writes a new record.
		log.Warn("ignored the owner record", "error", err)
	}
	if !ok {
		return s, "", nil
	}
	home := Home(root, record.SID)
	if err := platform.SecureDir(home, record.SID); err != nil {
		return nil, "", err
	}
	s.owner, s.bound, s.home = &record, true, kept{home}
	if s.profile, err = s.home.profile(); err != nil {
		log.Warn("ignored the kept profile", "error", err)
	}
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
	switch {
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

// Claim takes on caller when the service has no owner, is theirs, or the owner's tunnel is
// down. Whether that tunnel would come back at the owner's next sign-in does not hold the
// service: the owner may never sign in again. Administrators taking over a tunnel that runs
// (decision D5b) is still open; it would go here.
func (s *Service) Claim(caller peer.Identity) error {
	if !ValidSID(caller.UserID) {
		return fmt.Errorf("caller %q has no SID", caller.UserID)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case s.restarting:
		return control.ErrRestarting
	case s.owner != nil && s.owner.SID == caller.UserID:
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
	s.owner, s.restarting = &record, true
	s.platform.Restart()
	return control.ErrRestarting
}

func (s *Service) Applied(start *controlpb.StartRequest) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.profile = start
	s.keepLocked(true)
	if options := start.GetOptions(); options.GetSystemProxyEnabled() {
		return s.proxyOnLocked(uint16(options.GetMixedPort()))
	}
	return s.proxyOffLocked()
}

func (s *Service) Stopped() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.home.setWasRunning(false); err != nil {
		s.log.Error("keep was_running", "error", err)
	}
	return s.proxyOffLocked()
}

func (s *Service) Suspended() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.proxyOffLocked()
}

// SetSystemProxy turns the owner's system proxy on or off while the core runs; the choice
// is kept with the profile for the next sign-in.
func (s *Service) SetSystemProxy(enabled bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !enabled {
		if err := s.proxyOffLocked(); err != nil {
			return err
		}
	} else {
		if !s.running() || s.profile == nil {
			return engine.ErrNotRunning
		}
		if err := s.proxyOnLocked(uint16(s.profile.GetOptions().GetMixedPort())); err != nil {
			return err
		}
	}
	if s.profile != nil && s.profile.GetOptions().GetSystemProxyEnabled() != enabled {
		profile := proto.CloneOf(s.profile)
		if profile.Options == nil {
			profile.Options = &controlpb.RunOptions{}
		}
		profile.Options.SystemProxyEnabled = enabled
		s.profile = profile
		s.keepLocked(s.running())
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
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.bound {
		return
	}
	if err := s.proxyOffLocked(); err != nil {
		s.log.Warn("restore the owner's proxy settings", "error", err)
	}
}

// SignedIn follows a sign-in to session. The owner gets their tunnel back if it was up;
// a user whose proxy settings the service left behind gets them back.
func (s *Service) SignedIn(session uint32) {
	sid, err := s.platform.SessionUser(session)
	if err != nil {
		s.log.Warn("read the user of a new session", "session", session, "error", err)
		return
	}
	s.mu.Lock()
	if s.owner == nil || s.owner.SID != sid || !s.bound || s.restarting {
		s.mu.Unlock()
		s.restoreStrayProxy(sid, session)
		return
	}
	if s.owner.Session != session {
		s.owner.Session = session
		s.saveRecordLocked()
	}
	profile := s.profile
	resume := profile != nil && s.home.wasRunning() && !s.running()
	if !resume && s.proxyPort == 0 {
		if err := s.proxyOffLocked(); err != nil {
			s.log.Warn("restore the owner's proxy settings", "error", err)
		}
	}
	s.mu.Unlock()
	if !resume {
		return
	}
	if err := s.core.Resume(profile); err != nil {
		s.log.Error("bring the owner's tunnel back", "error", err)
		return
	}
	s.log.Info("brought the owner's tunnel back", "session", session)
}

// SignedOut stops the tunnel when its owner signs out of the session they use it from: the
// TUN takes every user's traffic (decision D5). It comes back at their next sign-in.
func (s *Service) SignedOut(session uint32) {
	s.mu.Lock()
	ours := s.owner != nil && s.bound && s.owner.Session == session
	s.mu.Unlock()
	if !ours || !s.running() {
		return
	}
	if err := s.core.Suspend(); err != nil {
		s.log.Error("stop the tunnel at the owner's sign-out", "error", err)
		return
	}
	s.log.Info("stopped the tunnel at the owner's sign-out", "session", session)
}

// keepLocked writes what the next sign-in needs. A failure only costs that, so it is
// logged rather than failing the request that already took effect.
func (s *Service) keepLocked(running bool) {
	if s.profile != nil {
		if err := s.home.saveProfile(s.profile); err != nil {
			s.log.Error("keep the profile", "error", err)
		}
	}
	if err := s.home.setWasRunning(running); err != nil {
		s.log.Error("keep was_running", "error", err)
	}
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

// proxyOnLocked points the owner's system proxy at the mixed port, first keeping their own
// settings to restore.
func (s *Service) proxyOnLocked(port uint16) error {
	if port == 0 {
		return errors.New("the system proxy needs a mixed port")
	}
	if s.proxyPort == port {
		return nil
	}
	session, err := s.sessionLocked()
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
	s.proxyPort = port
	return nil
}

// proxyOffLocked gives the owner their own settings back. When they are not signed in, the
// settings wait in their home for the next sign-in.
func (s *Service) proxyOffLocked() error {
	s.proxyPort = 0
	previous, saved, err := s.home.previousProxy()
	if err != nil || !saved {
		return err
	}
	session, err := s.sessionLocked()
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
