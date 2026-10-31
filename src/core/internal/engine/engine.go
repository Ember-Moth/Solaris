// Package engine embeds mihomo for the control dispatcher (docs/architecture.md §3).
//
// mihomo keeps its runtime in package-level state: the hub and its executor, the tunnel,
// the listener's inbounds, statistic, the resolver, the log, the updater and the cache
// file. A process therefore holds at most one Engine, and this is the only package that
// touches that state; imports_test.go holds every other package to it. internal/admission
// prepares each configuration for mihomo's parser, and internal/compose and
// internal/vpngate use mihomo's stateless parsers and types, so a mihomo upgrade is
// checked there as well as here.
//
// The methods answer with the control protocol's messages (controlpb) rather than types
// of their own: the dispatcher is their only caller, and the mapping from mihomo's types
// stays here, since nothing else imports mihomo's runtime (D31).
package engine

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"sync"
	"sync/atomic"
	"time"

	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	"github.com/metacubex/mihomo/hub/route"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

var (
	// ErrInvalidConfig and ErrUnsupported are admission's own, so that errors.Is finds
	// them whichever package returned the error.
	ErrInvalidConfig   = admission.ErrInvalidConfig
	ErrNotRunning      = errors.New("the core is not running")
	ErrAlreadyRunning  = errors.New("the core is already running")
	ErrUnsupported     = admission.ErrUnsupported
	ErrInvalidArgument = errors.New("invalid argument")
	ErrApplyFailed     = errors.New("the configuration did not apply")
)

const mihomoModule = "github.com/metacubex/mihomo"

type Options struct {
	// HomeDir is the owner's mihomo home directory and the root of IsSafePath. mihomo
	// opens cache.db there once per process, so it cannot change while the process runs.
	HomeDir string
	Tun     admission.TunMode
	// TunFD is the host's TUN descriptor in fd mode. The engine never closes it: mihomo
	// runs on duplicates (hostTUN), so the host closes it once Close returns.
	TunFD int
	// IncludeAllNetworks says that the fd-mode host's tunnel includes all networks: the
	// macOS extension's includeAllNetworks (D19). mihomo's system and mixed stacks do not
	// work there, so the pipeline replaces them with gVisor.
	IncludeAllNetworks bool
}

type Engine struct {
	tunMode admission.TunMode
	home    string
	// includeAllNetworks is Options.IncludeAllNetworks in fd mode.
	includeAllNetworks bool
	// tun is the host's TUN in fd mode, nil otherwise; the lifecycle lock guards it.
	tun *hostTUN

	// lifecycle serializes Validate, Start, Reload, Stop and Close; mihomo parses into
	// global state and must not do two of them at once.
	lifecycle sync.Mutex

	// geo refreshes the GEO data while a configuration asks for it; the lifecycle lock
	// guards it.
	geo *geoUpdater

	mu        sync.RWMutex
	state     controlpb.RunState
	startedAt time.Time
	lastError error
	closed    bool
	// entries ties the running rules to the plan's rule entries; nil for a profile's own rules.
	entries *ruleEntries

	// meshMu guards the running mesh outbounds (mesh_run.go), by name and in order.
	meshMu    sync.Mutex
	mesh      map[string]*meshOutbound
	meshOrder []string
	// meshStarts are the starts runMesh set off, which the core waits for as it stops.
	meshStarts sync.WaitGroup
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
	Version()
	engine := &Engine{
		tunMode:            options.Tun,
		home:               home,
		includeAllNetworks: options.Tun == admission.TunFD && options.IncludeAllNetworks,
		state:              controlpb.RunState_RUN_STATE_STOPPED,
	}
	if options.Tun == admission.TunFD {
		if engine.tun, err = newHostTUN(options.TunFD); err != nil {
			return nil, err
		}
	}
	return engine, nil
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

// Version is mihomo's version. The Windows service answers GetInfo with it before it has an
// owner, and so an Engine (docs/architecture.md §5.4).
func Version() string {
	coreOnce.Do(func() {
		coreVerStr = moduleVersion()
		C.Version = coreVerStr
	})
	return coreVerStr
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

func (e *Engine) TunMode() admission.TunMode { return e.tunMode }

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
