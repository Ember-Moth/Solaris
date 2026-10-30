// Package engine embeds mihomo for the control dispatcher (docs/architecture.md §3).
//
// mihomo keeps its state in package-level variables, so a process holds at most one
// Engine. The package is the only place that calls into mihomo; upgrades change it alone.
package engine

import (
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
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

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
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
	// TunFD is the host's TUN descriptor in fd mode. The engine never closes it: mihomo
	// runs on duplicates (hostTUN), so the host closes it once Close returns.
	TunFD int
	// IncludeAllNetworks says that the fd-mode host's tunnel includes all networks: the
	// macOS extension's includeAllNetworks (D19). mihomo's system and mixed stacks do not
	// work there, so the pipeline replaces them with gVisor.
	IncludeAllNetworks bool
}

type Engine struct {
	tunMode TunMode
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
		includeAllNetworks: options.Tun == TunFD && options.IncludeAllNetworks,
		state:              controlpb.RunState_RUN_STATE_STOPPED,
	}
	if options.Tun == TunFD {
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

// Validate runs the admission pipeline without applying the result. A composition
// comes back with its report; a profile's own YAML with none.
func (e *Engine) Validate(profile Profile) (*controlpb.CompositionReport, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return nil, err
	}
	admitted, err := e.admit(profile)
	if err != nil {
		return nil, err
	}
	return admitted.report, nil
}

func (e *Engine) admit(profile Profile) (*admission, error) {
	homeInUse = e.home
	profile.Options.includeAllNetworks = e.includeAllNetworks
	return admitProfile(profile, e.tunMode)
}

// apply puts an admitted configuration in place and keeps what goes with it.
func (e *Engine) apply(admitted *admission) {
	mesh := e.adoptMesh(admitted)
	closeReplaced(mesh)
	hub.ApplyConfig(admitted.cfg)
	e.settleTUN()
	e.setEntries(admitted.entries)
	e.syncGeoUpdater(admitted.cfg)
	e.runMesh(mesh)
}

// prepareTUN gives mihomo the descriptor its TUN runs on in fd mode, right before apply.
func (e *Engine) prepareTUN(cfg *config.Config) error {
	if e.tun == nil {
		return nil
	}
	return e.tun.prepare(&cfg.General.Tun, listener.LastTunConf)
}

// settleTUN lets go of a duplicate mihomo no longer runs on, after each change to its TUN.
func (e *Engine) settleTUN() {
	if e.tun != nil {
		e.tun.settle(listener.LastTunConf)
	}
}

// Start applies a profile to a stopped core.
func (e *Engine) Start(profile Profile) (*controlpb.CompositionReport, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return nil, err
	}
	if state := e.State(); state == controlpb.RunState_RUN_STATE_RUNNING {
		return nil, ErrAlreadyRunning
	}
	admitted, err := e.admit(profile)
	if err != nil {
		return nil, err
	}
	cfg := admitted.cfg
	if err := probePorts(cfg); err != nil {
		e.setState(controlpb.RunState_RUN_STATE_FAILED, err)
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	if err := e.prepareTUN(cfg); err != nil {
		e.setState(controlpb.RunState_RUN_STATE_FAILED, err)
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.setState(controlpb.RunState_RUN_STATE_STARTING, nil)
	e.apply(admitted)
	if err := checkApplied(cfg); err != nil {
		e.teardown()
		e.setState(controlpb.RunState_RUN_STATE_FAILED, err)
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.setState(controlpb.RunState_RUN_STATE_RUNNING, nil)
	return admitted.report, nil
}

// Reload applies a profile in place. mihomo keeps the TUN when its settings are unchanged.
func (e *Engine) Reload(profile Profile) (*controlpb.CompositionReport, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return nil, err
	}
	if !e.running() {
		return nil, ErrNotRunning
	}
	admitted, err := e.admit(profile)
	if err != nil {
		return nil, err
	}
	cfg := admitted.cfg
	// The core keeps the profile it runs, and its ports, when a new port is taken.
	if err := probePorts(cfg); err != nil {
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	if err := e.prepareTUN(cfg); err != nil {
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.apply(admitted)
	if err := checkApplied(cfg); err != nil {
		// The rest of the profile applied; the core keeps running and reports the gap.
		e.mu.Lock()
		e.lastError = err
		e.mu.Unlock()
		return nil, fmt.Errorf("%w: %w", ErrApplyFailed, err)
	}
	e.mu.Lock()
	e.lastError = nil
	e.mu.Unlock()
	return admitted.report, nil
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

type inboundPort struct {
	name       string
	want, have int
	udp        bool // the inbound also listens for UDP on the port
}

// inboundPorts pairs the ports cfg asks for with the ones open now.
func inboundPorts(cfg *config.Config) []inboundPort {
	general := cfg.General
	ports := listener.GetPorts()
	return []inboundPort{
		{"port", general.Port, ports.Port, false},
		{"socks-port", general.SocksPort, ports.SocksPort, true},
		{"mixed-port", general.MixedPort, ports.MixedPort, true},
		{"redir-port", general.RedirPort, ports.RedirPort, false},
		{"tproxy-port", general.TProxyPort, ports.TProxyPort, false},
	}
}

// probePorts opens and closes each inbound port that cfg adds, so that a port another
// program holds fails the request with the reason before anything changes.
// hub.ApplyConfig closes the old port before it opens the new one and only logs why
// that failed, which would leave a running core without its port.
func probePorts(cfg *config.Config) error {
	general := cfg.General
	ports := inboundPorts(cfg)
	open := make(map[int]bool, len(ports))
	for _, port := range ports {
		open[port.have] = true
	}
	for _, port := range ports {
		if port.want == 0 || open[port.want] {
			continue
		}
		address := inboundAddress(general.BindAddress, port.want, general.AllowLan)
		tcp, err := net.Listen("tcp", address)
		if err != nil {
			return err
		}
		tcp.Close()
		if port.udp {
			udp, err := net.ListenPacket("udp", address)
			if err != nil {
				return err
			}
			udp.Close()
		}
	}
	return nil
}

// inboundAddress is where mihomo's listener package opens an inbound (its genAddr).
func inboundAddress(bindAddress string, port int, allowLAN bool) string {
	switch {
	case !allowLAN:
		bindAddress = "127.0.0.1"
	case bindAddress == "*":
		bindAddress = ""
	}
	return net.JoinHostPort(bindAddress, strconv.Itoa(port))
}

// checkApplied reports what hub.ApplyConfig only logged: an inbound port or the TUN
// that could not open.
func checkApplied(cfg *config.Config) error {
	general := cfg.General
	var missing []string
	for _, port := range inboundPorts(cfg) {
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
	e.syncGeoUpdater(nil)
	e.setEntries(nil)
	providers := tunnel.Providers()
	ruleProviders := tunnel.RuleProviders()
	executor.Shutdown()
	if idle, err := idleConfig(); err != nil {
		log.Errorln("[engine] idle configuration: %s", err)
	} else {
		hub.ApplyConfig(idle)
	}
	e.closeMesh()
	e.settleTUN()
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
