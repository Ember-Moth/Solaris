package owner

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	"google.golang.org/protobuf/encoding/protojson"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/sysproxy"
)

// What the service keeps in the owner's home besides mihomo's own files (§5.1). Only SYSTEM
// writes there; the owner may read it.
const (
	// profileFile is the last profile the core took, as Start carries it: the source (a
	// subscription's YAML or a composition), the overrides and the run options.
	profileFile = "profile.json"
	// stateFile holds was_running: whether the tunnel comes back at the next sign-in.
	stateFile = "state.json"
	// proxyFile holds the owner's own proxy settings while the service's are in place, so
	// that they come back even after a crash.
	proxyFile         = "proxy.json"
	resumeAttemptFile = "resume-attempt.json"
)

type kept struct{ home string }

// An unfinished automatic start survives a process crash. On the next start it blocks
// another automatic attempt until the user connects, preventing an SCM crash loop.
func (k kept) resumeInterrupted() bool {
	_, err := os.Stat(k.path(resumeAttemptFile))
	return err == nil
}

func (k kept) markResumeAttempt() error {
	raw, err := json.Marshal(struct {
		StartedAt int64 `json:"started_at_unix_ms"`
	}{time.Now().UnixMilli()})
	if err != nil {
		return err
	}
	return writeFile(k.path(resumeAttemptFile), raw)
}

func (k kept) clearResumeAttempt() error {
	err := os.Remove(k.path(resumeAttemptFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	return err
}

type state struct {
	WasRunning bool `json:"was_running"`
}

func (k kept) path(name string) string { return filepath.Join(k.home, name) }

// profile is nil when the owner never started the core.
func (k kept) profile() (*controlpb.StartRequest, error) {
	raw, err := os.ReadFile(k.path(profileFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	start := &controlpb.StartRequest{}
	if err := protojson.Unmarshal(raw, start); err != nil {
		return nil, err
	}
	return start, nil
}

func (k kept) saveProfile(start *controlpb.StartRequest) error {
	raw, err := protojson.Marshal(start)
	if err != nil {
		return err
	}
	return writeFile(k.path(profileFile), raw)
}

func (k kept) wasRunning() bool {
	var saved state
	raw, err := os.ReadFile(k.path(stateFile))
	return err == nil && json.Unmarshal(raw, &saved) == nil && saved.WasRunning
}

func (k kept) setWasRunning(running bool) error {
	raw, err := json.Marshal(state{WasRunning: running})
	if err != nil {
		return err
	}
	return writeFile(k.path(stateFile), raw)
}

// previousProxy is the owner's own proxy settings, while the service's are in place.
func (k kept) previousProxy() (sysproxy.Settings, bool, error) {
	raw, err := os.ReadFile(k.path(proxyFile))
	if errors.Is(err, fs.ErrNotExist) {
		return sysproxy.Settings{}, false, nil
	}
	if err != nil {
		return sysproxy.Settings{}, false, err
	}
	var settings sysproxy.Settings
	if err := json.Unmarshal(raw, &settings); err != nil {
		return sysproxy.Settings{}, false, err
	}
	return settings, true, nil
}

func (k kept) savePreviousProxy(settings sysproxy.Settings) error {
	raw, err := json.Marshal(settings)
	if err != nil {
		return err
	}
	return writeFile(k.path(proxyFile), raw)
}

func (k kept) dropPreviousProxy() error {
	if err := os.Remove(k.path(proxyFile)); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}
