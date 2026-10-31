package engine

import (
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"

	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/hub"
	"github.com/metacubex/mihomo/hub/executor"
	"github.com/metacubex/mihomo/listener"
	"github.com/metacubex/mihomo/log"
	"github.com/metacubex/mihomo/tunnel"
	"github.com/metacubex/mihomo/tunnel/statistic"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Validate runs the admission pipeline without applying the result. A composition
// comes back with its report; a profile's own YAML with none.
func (e *Engine) Validate(profile admission.Profile) (*controlpb.CompositionReport, error) {
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

// apply puts an admitted configuration in place and keeps what goes with it.
func (e *Engine) apply(admitted *admittedConfig) {
	mesh := e.adoptMesh(admitted)
	closeReplaced(mesh)
	applyConfig(admitted.cfg)
	e.settleTUN()
	e.setEntries(admitted.entries)
	e.clearTCPDelays()
	e.syncGeoUpdater(admitted.cfg)
	e.runMesh(mesh)
}

// applyConfig is hub.ApplyConfig, once the finalizers that are due have run when the race
// detector is on (race.go).
func applyConfig(cfg *config.Config) {
	settleFinalizers()
	hub.ApplyConfig(cfg)
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
func (e *Engine) Start(profile admission.Profile) (*controlpb.CompositionReport, error) {
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
func (e *Engine) Reload(profile admission.Profile) (*controlpb.CompositionReport, error) {
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
	admitted, err := admitProfile(admission.Profile{Config: []byte(source)}, admission.Host{Tun: admission.TunNone})
	if err != nil {
		return nil, err
	}
	return admitted.cfg, nil
}

// teardown takes the running configuration down. executor.Shutdown alone is meant for
// process exit: it closes the TUN but leaves the ports, DNS server and providers.
func (e *Engine) teardown() {
	e.syncGeoUpdater(nil)
	e.setEntries(nil)
	e.clearTCPDelays()
	proxies := tunnel.Proxies()
	providers := tunnel.Providers()
	ruleProviders := tunnel.RuleProviders()
	executor.Shutdown()
	closeInbounds()
	if idle, err := idleConfig(); err != nil {
		log.Errorln("[engine] idle configuration: %s", err)
	} else {
		applyConfig(idle)
	}
	e.closeMesh()
	e.settleTUN()
	statistic.DefaultManager.Range(func(c statistic.Tracker) bool {
		_ = c.Close()
		return true
	})
	// mihomo leaves the outbounds to the garbage collector, which closes them from
	// finalizers some time later; with their connections gone, they close now.
	for _, proxy := range proxies {
		_ = proxy.Close()
	}
	for _, provider := range providers {
		for _, proxy := range provider.Proxies() {
			_ = proxy.Close()
		}
		closeProvider(provider)
	}
	for _, provider := range ruleProviders {
		closeProvider(provider)
	}
}

// closeInbounds closes the inbounds that check each connection against the LAN and
// authentication settings: hub.ApplyConfig sets those, which the inbounds read without a
// lock, before it closes the inbounds.
func closeInbounds() {
	listener.PatchInboundListeners(nil, tunnel.Tunnel, true)
	listener.ReCreateHTTP(0, tunnel.Tunnel)
	listener.ReCreateSocks(0, tunnel.Tunnel)
	listener.ReCreateMixed(0, tunnel.Tunnel)
}

func closeProvider(provider any) {
	if closer, ok := provider.(io.Closer); ok {
		_ = closer.Close()
	}
}
