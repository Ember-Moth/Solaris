// Package engine embeds mihomo for the control dispatcher (docs/architecture.md §3).
//
// mihomo keeps its state in package-level variables, so a process holds at most one
// Engine. The package is the only place that calls into mihomo; upgrades change it alone.
package engine

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	"github.com/metacubex/mihomo/hub"
	"github.com/metacubex/mihomo/hub/executor"
	"github.com/metacubex/mihomo/hub/route"
	"github.com/metacubex/mihomo/listener"
	"github.com/metacubex/mihomo/log"
	"github.com/metacubex/mihomo/tunnel"
	"github.com/metacubex/mihomo/tunnel/statistic"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
)

var (
	ErrInvalidConfig   = errors.New("invalid configuration")
	ErrNotRunning      = errors.New("the core is not running")
	ErrAlreadyRunning  = errors.New("the core is already running")
	ErrUnsupported     = errors.New("not supported by this host")
	ErrInvalidArgument = errors.New("invalid argument")
	ErrApplyFailed     = errors.New("the configuration did not apply")
)

const mihomoModule = "github.com/metacubex/mihomo"

type Options struct {
	// HomeDir is the owner's mihomo home directory and the root of IsSafePath. mihomo
	// opens cache.db there once per process, so it cannot change while the process runs.
	HomeDir string
	Tun     TunMode
}

type Engine struct {
	tunMode TunMode
	home    string

	// lifecycle serializes Validate, Start, Reload, Stop and Close; mihomo parses into
	// global state and must not do two of them at once.
	lifecycle sync.Mutex

	mu        sync.RWMutex
	state     controlpb.RunState
	startedAt time.Time
	lastError error
	closed    bool
}

var (
	open atomic.Bool
	// homeInUse is the home mihomo opened cache.db in; later engines must reuse it.
	homeInUse  string
	coreOnce   sync.Once
	coreVerStr string
)

// New initializes mihomo for the given owner and mode. It fails if another Engine is
// open, or if mihomo's path sandbox is not intact.
func New(options Options) (*Engine, error) {
	if !open.CompareAndSwap(false, true) {
		return nil, errors.New("a mihomo engine is already open in this process")
	}
	engine, err := newEngine(options)
	if err != nil {
		open.Store(false)
		return nil, err
	}
	return engine, nil
}

func newEngine(options Options) (*Engine, error) {
	if options.HomeDir == "" {
		return nil, errors.New("engine needs a home directory")
	}
	home, err := filepath.Abs(options.HomeDir)
	if err != nil {
		return nil, err
	}
	if homeInUse != "" && homeInUse != home {
		return nil, fmt.Errorf("mihomo already uses %s in this process; a new home needs a new process", homeInUse)
	}
	if err := os.MkdirAll(home, 0o700); err != nil {
		return nil, err
	}
	// Second line of defense: should a controller ever start, it cannot restart the
	// process, rewrite the configuration or upgrade the core (§3.3 #3).
	route.SetEmbedMode(true)
	C.SetHomeDir(home)
	if err := checkPathSandbox(home); err != nil {
		return nil, err
	}
	coreOnce.Do(func() {
		coreVerStr = moduleVersion()
		C.Version = coreVerStr
	})
	return &Engine{tunMode: options.Tun, home: home, state: controlpb.RunState_RUN_STATE_STOPPED}, nil
}

// checkPathSandbox refuses to run when IsSafePath no longer confines files to the home
// directory: a cmfa build, SKIP_SAFE_PATH_CHECK, or extra SAFE_PATHS (§3.3 #1 and #4).
// mihomo reads the variables once at start-up, so the check tests the behavior.
func checkPathSandbox(home string) error {
	if features.CMFA {
		return errors.New("built with the cmfa tag, which turns mihomo's IsSafePath off")
	}
	if got := C.Path.HomeDir(); got != home {
		return fmt.Errorf("mihomo home is %s, want %s", got, home)
	}
	if paths := C.Path.SafePaths(); len(paths) != 1 {
		return fmt.Errorf("SAFE_PATHS widens mihomo's path sandbox to %v", paths)
	}
	if C.Path.IsSafePath(filepath.Join(filepath.Dir(home), "outside-home")) {
		return errors.New("SKIP_SAFE_PATH_CHECK turns mihomo's path sandbox off")
	}
	if !C.Path.IsSafePath(filepath.Join(home, "providers", "probe")) {
		return errors.New("mihomo's path sandbox rejects its own home directory")
	}
	return nil
}

func moduleVersion() string {
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range info.Deps {
			if dep.Path == mihomoModule {
				if dep.Replace != nil {
					dep = dep.Replace
				}
				return dep.Version
			}
		}
	}
	return C.Version
}

// Close stops the core and lets another Engine open in this process.
func (e *Engine) Close() error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if e.closed {
		return nil
	}
	if e.State() != controlpb.RunState_RUN_STATE_STOPPED {
		e.teardown()
	}
	e.mu.Lock()
	e.closed = true
	e.state = controlpb.RunState_RUN_STATE_STOPPED
	e.mu.Unlock()
	open.Store(false)
	return nil
}

func (e *Engine) CoreVersion() string { return coreVerStr }

func (e *Engine) TunMode() TunMode { return e.tunMode }

func (e *Engine) HomeDir() string { return e.home }

func (e *Engine) State() controlpb.RunState {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.state
}

func (e *Engine) running() bool {
	return e.State() == controlpb.RunState_RUN_STATE_RUNNING
}

func (e *Engine) setState(state controlpb.RunState, err error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.state = state
	e.lastError = err
	if state == controlpb.RunState_RUN_STATE_RUNNING && e.startedAt.IsZero() {
		e.startedAt = time.Now()
	}
	if state != controlpb.RunState_RUN_STATE_RUNNING {
		e.startedAt = time.Time{}
	}
}

func (e *Engine) checkOpen() error {
	e.mu.RLock()
	defer e.mu.RUnlock()
	if e.closed {
		return errors.New("the engine is closed")
	}
	return nil
}

// Validate runs the admission pipeline without applying the result.
func (e *Engine) Validate(profile Profile) error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return err
	}
	_, err := e.admit(profile)
	return err
}

func (e *Engine) admit(profile Profile) (*config.Config, error) {
	homeInUse = e.home
	return admit(profile, e.tunMode)
}

// Start applies a profile to a stopped core.
func (e *Engine) Start(profile Profile) error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return err
	}
	if state := e.State(); state == controlpb.RunState_RUN_STATE_RUNNING {
		return ErrAlreadyRunning
	}
	cfg, err := e.admit(profile)
	if err != nil {
		return err
	}
	e.setState(controlpb.RunState_RUN_STATE_STARTING, nil)
	hub.ApplyConfig(cfg)
	if err := checkApplied(cfg); err != nil {
		e.teardown()
		e.setState(controlpb.RunState_RUN_STATE_FAILED, err)
		return fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.setState(controlpb.RunState_RUN_STATE_RUNNING, nil)
	return nil
}

// Reload applies a profile in place. mihomo keeps the TUN when its settings are unchanged.
func (e *Engine) Reload(profile Profile) error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return err
	}
	if !e.running() {
		return ErrNotRunning
	}
	cfg, err := e.admit(profile)
	if err != nil {
		return err
	}
	hub.ApplyConfig(cfg)
	if err := checkApplied(cfg); err != nil {
		// The rest of the profile applied; the core keeps running and reports the gap.
		e.mu.Lock()
		e.lastError = err
		e.mu.Unlock()
		return fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.mu.Lock()
	e.lastError = nil
	e.mu.Unlock()
	return nil
}

// Stop returns the core to its idle state. Stopping a stopped core does nothing.
func (e *Engine) Stop() error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return err
	}
	switch e.State() {
	case controlpb.RunState_RUN_STATE_STOPPED:
		return nil
	case controlpb.RunState_RUN_STATE_RUNNING:
		e.setState(controlpb.RunState_RUN_STATE_STOPPING, nil)
		e.teardown()
	}
	e.setState(controlpb.RunState_RUN_STATE_STOPPED, nil)
	return nil
}

// checkApplied reports what hub.ApplyConfig only logged: an inbound port or the TUN
// that could not open.
func checkApplied(cfg *config.Config) error {
	general := cfg.General
	ports := listener.GetPorts()
	var missing []string
	for _, port := range []struct {
		name       string
		want, have int
	}{
		{"port", general.Port, ports.Port},
		{"socks-port", general.SocksPort, ports.SocksPort},
		{"mixed-port", general.MixedPort, ports.MixedPort},
		{"redir-port", general.RedirPort, ports.RedirPort},
		{"tproxy-port", general.TProxyPort, ports.TProxyPort},
	} {
		if port.want != 0 && port.want != port.have {
			missing = append(missing, fmt.Sprintf("%s %d", port.name, port.want))
		}
	}
	if general.Tun.Enable && !listener.GetTunConf().Enable {
		missing = append(missing, "the TUN")
	}
	if len(missing) != 0 {
		return fmt.Errorf("could not open %s; the mihomo log has the cause", strings.Join(missing, ", "))
	}
	return nil
}

// idleConfig closes every inbound, the TUN and the DNS server and drops the proxies,
// keeping the mode so a later Start resumes it.
func idleConfig() (*config.Config, error) {
	source := fmt.Sprintf("mode: %s\nlog-level: %s\n", tunnel.Mode(), log.Level())
	return admit(Profile{Config: []byte(source)}, TunNone)
}

// teardown takes the running configuration down. executor.Shutdown alone is meant for
// process exit: it closes the TUN but leaves the ports, DNS server and providers.
func (e *Engine) teardown() {
	providers := tunnel.Providers()
	ruleProviders := tunnel.RuleProviders()
	executor.Shutdown()
	if idle, err := idleConfig(); err != nil {
		log.Errorln("[engine] idle configuration: %s", err)
	} else {
		hub.ApplyConfig(idle)
	}
	statistic.DefaultManager.Range(func(c statistic.Tracker) bool {
		_ = c.Close()
		return true
	})
	for _, provider := range providers {
		closeProvider(provider)
	}
	for _, provider := range ruleProviders {
		closeProvider(provider)
	}
}

func closeProvider(provider any) {
	if closer, ok := provider.(io.Closer); ok {
		_ = closer.Close()
	}
}
