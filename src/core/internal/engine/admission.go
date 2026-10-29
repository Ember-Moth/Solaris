package engine

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"path/filepath"
	"strings"

	"github.com/metacubex/mihomo/common/yaml"
	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/hub/executor"
	"github.com/metacubex/mihomo/listener/inbound"
)

// TunMode decides who owns the TUN and the routes (docs/architecture.md §3.1). It is
// fixed when the engine is created.
type TunMode int

const (
	// TunNone never creates a TUN and keeps every inbound on 127.0.0.1. The development
	// server runs on the developer's own machine and uses it (§11).
	TunNone TunMode = iota
	// TunOwned lets mihomo create the TUN and set auto-route, auto-detect-interface and
	// strict-route: the Windows service, and the TUN tests in CI.
	TunOwned
	// TunFD runs on a descriptor the macOS extension passes in; the host routes. It
	// arrives with the extension in M5.
	TunFD
)

func (m TunMode) String() string {
	switch m {
	case TunNone:
		return "no-tun"
	case TunOwned:
		return "owned-tun"
	case TunFD:
		return "fd"
	default:
		return fmt.Sprintf("TunMode(%d)", int(m))
	}
}

// RunOptions are the host's settings that the pipeline lays over the user's profile.
type RunOptions struct {
	TUN bool
	// MixedPort replaces the profile's mixed-port when non-zero.
	MixedPort uint16
	// AllowLAN opts in to listening beyond 127.0.0.1; without it every inbound stays on
	// loopback whatever the profile says.
	AllowLAN bool
}

// Profile is one submission from the UI: the final YAML, the override and the options.
type Profile struct {
	Config []byte
	// Override is a JSON object merged into the configuration before the platform
	// patches: objects merge key by key, null removes a key, anything else replaces.
	// The full override scope is still open (decision D4).
	Override []byte
	Options  RunOptions
}

const loopback = "127.0.0.1"

// controllerKeys open mihomo's own controller or files it serves. The pipeline clears
// them last, after the override (§3.3 #2); the external UI keys go too because the
// updater would download a dashboard into the home directory for a controller that never runs.
var controllerKeys = []string{
	"external-controller",
	"external-controller-tls",
	"external-controller-unix",
	"external-controller-pipe",
	"external-doh-server",
	"secret",
	"external-ui",
	"external-ui-url",
	"external-ui-name",
}

// systemListenerTypes take over the system's traffic: a TUN, or redirected and
// transparent-proxy sockets that need routes or firewall rules. The TUN is only ever the
// one the tun section describes, so the pipeline drops these from listeners in every mode.
var systemListenerTypes = map[string]bool{"tun": true, "redir": true, "tproxy": true}

func takesOverSystem(listener C.InboundListener) bool {
	switch listener.(type) {
	case *inbound.Tun, *inbound.Redir, *inbound.TProxy:
		return true
	}
	return false
}

var errFDModePending = errors.New("fd mode arrives with the macOS extension (M5)")

// admit runs the configuration admission pipeline (docs/architecture.md §3.2): user
// YAML → override → platform patches for the TUN mode → security items, which always
// come last → executor.ParseWithBytes → a check of the parsed result.
func admit(profile Profile, mode TunMode) (*config.Config, error) {
	if profile.Options.TUN && mode == TunNone {
		return nil, fmt.Errorf("%w: this host never creates a TUN", ErrUnsupported)
	}
	doc, err := decodeDocument(profile.Config)
	if err != nil {
		return nil, invalidConfig(err)
	}
	if err := applyOverride(doc, profile.Override); err != nil {
		return nil, invalidConfig(err)
	}
	options := profile.Options
	switch mode {
	case TunNone:
		options.TUN, options.AllowLAN = false, false
		err = patchNoTUN(doc, options)
	case TunOwned:
		err = patchOwnedTUN(doc, options)
	case TunFD:
		return nil, fmt.Errorf("%w: %w", ErrUnsupported, errFDModePending)
	default:
		return nil, fmt.Errorf("%w: unknown TUN mode %v", ErrUnsupported, mode)
	}
	if err == nil {
		err = dropSystemListeners(doc)
	}
	if err != nil {
		return nil, invalidConfig(err)
	}
	if err := enforceSecurity(doc); err != nil {
		return nil, invalidConfig(err)
	}
	raw, err := yaml.Marshal(doc)
	if err != nil {
		return nil, invalidConfig(err)
	}
	cfg, err := executor.ParseWithBytes(raw)
	if err != nil {
		return nil, invalidConfig(err)
	}
	if err := verifyAdmitted(cfg, mode, options); err != nil {
		return nil, invalidConfig(err)
	}
	return cfg, nil
}

func invalidConfig(err error) error {
	return fmt.Errorf("%w: %w", ErrInvalidConfig, err)
}

// decodeDocument reads the profile as a generic mapping. Decoding into a map resolves
// anchors and merge keys, so a patch cannot be bypassed by an alias.
func decodeDocument(source []byte) (map[string]any, error) {
	if len(bytes.TrimSpace(source)) == 0 {
		return nil, errors.New("the configuration is empty")
	}
	var doc map[string]any
	if err := yaml.Unmarshal(source, &doc); err != nil {
		return nil, err
	}
	if doc == nil {
		return nil, errors.New("the configuration is not a YAML mapping")
	}
	return doc, nil
}

func applyOverride(doc map[string]any, override []byte) error {
	if len(bytes.TrimSpace(override)) == 0 {
		return nil
	}
	decoder := json.NewDecoder(bytes.NewReader(override))
	decoder.UseNumber()
	var patch map[string]any
	if err := decoder.Decode(&patch); err != nil {
		return fmt.Errorf("override: %w", err)
	}
	if decoder.More() {
		return errors.New("override: trailing data after the JSON object")
	}
	if patch == nil {
		return errors.New("override: not a JSON object")
	}
	return mergeInto(doc, patch)
}

func mergeInto(target map[string]any, patch map[string]any) error {
	for key, value := range patch {
		if value == nil {
			delete(target, key)
			continue
		}
		value, err := fromJSON(value)
		if err != nil {
			return fmt.Errorf("override %s: %w", key, err)
		}
		if nested, ok := value.(map[string]any); ok {
			if existing, ok := target[key].(map[string]any); ok {
				if err := mergeInto(existing, nested); err != nil {
					return err
				}
				continue
			}
		}
		target[key] = value
	}
	return nil
}

// fromJSON turns json.Number into the integer or float YAML expects.
func fromJSON(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		if n, err := v.Int64(); err == nil {
			return n, nil
		}
		return v.Float64()
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			converted, err := fromJSON(item)
			if err != nil {
				return nil, err
			}
			out[i] = converted
		}
		return out, nil
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			converted, err := fromJSON(item)
			if err != nil {
				return nil, err
			}
			out[key] = converted
		}
		return out, nil
	default:
		return v, nil
	}
}

// section returns doc[key] as a mapping, replacing anything else; the pipeline owns the
// keys it writes there.
func section(doc map[string]any, key string) map[string]any {
	if existing, ok := doc[key].(map[string]any); ok {
		return existing
	}
	created := map[string]any{}
	doc[key] = created
	return created
}

// patchNoTUN applies the development guardrails (§11): no TUN and nothing that needs
// routes or firewall rules, and every inbound on loopback.
func patchNoTUN(doc map[string]any, options RunOptions) error {
	doc["tun"] = map[string]any{
		"enable":                false,
		"auto-route":            false,
		"auto-redirect":         false,
		"auto-detect-interface": false,
		"strict-route":          false,
	}
	doc["redir-port"] = 0
	doc["tproxy-port"] = 0
	setMixedPort(doc, options)
	return restrictToLoopback(doc)
}

// patchOwnedTUN writes the TUN section mihomo creates and routes itself; the user's
// stack, device, MTU and exclusions stay.
func patchOwnedTUN(doc map[string]any, options RunOptions) error {
	tun := section(doc, "tun")
	tun["enable"] = options.TUN
	tun["auto-route"] = true
	tun["auto-detect-interface"] = true
	tun["strict-route"] = true
	tun["auto-redirect"] = false
	tun["file-descriptor"] = 0
	if options.TUN {
		// The TUN hijacks DNS into mihomo's resolver, which only exists when DNS is on.
		section(doc, "dns")["enable"] = true
	}
	setMixedPort(doc, options)
	if options.AllowLAN {
		doc["allow-lan"] = true
		return nil
	}
	return restrictToLoopback(doc)
}

func setMixedPort(doc map[string]any, options RunOptions) {
	if options.MixedPort != 0 {
		doc["mixed-port"] = int(options.MixedPort)
	}
}

func dropSystemListeners(doc map[string]any) error {
	listeners, err := listenerMaps(doc)
	if err != nil {
		return err
	}
	kept := make([]any, 0, len(listeners))
	for _, listener := range listeners {
		kind, _ := listener["type"].(string)
		if !systemListenerTypes[strings.ToLower(kind)] {
			kept = append(kept, listener)
		}
	}
	if _, ok := doc["listeners"]; ok {
		doc["listeners"] = kept
	}
	return nil
}

func listenerMaps(doc map[string]any) ([]map[string]any, error) {
	raw, ok := doc["listeners"]
	if !ok || raw == nil {
		return nil, nil
	}
	items, ok := raw.([]any)
	if !ok {
		return nil, errors.New("listeners must be a list")
	}
	out := make([]map[string]any, 0, len(items))
	for i, item := range items {
		listener, ok := item.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("listeners[%d] must be a mapping", i)
		}
		out = append(out, listener)
	}
	return out, nil
}

// restrictToLoopback keeps every inbound on 127.0.0.1: the ports governed by allow-lan,
// and the listeners, tunnels, DNS server and legacy server inbounds that name their own address.
func restrictToLoopback(doc map[string]any) error {
	doc["allow-lan"] = false
	doc["bind-address"] = loopback
	listeners, err := listenerMaps(doc)
	if err != nil {
		return err
	}
	for _, listener := range listeners {
		listener["listen"] = loopback
	}
	if err := loopbackTunnels(doc); err != nil {
		return err
	}
	for _, key := range []string{"dns", "tuic-server"} {
		block, ok := doc[key].(map[string]any)
		if !ok {
			continue
		}
		if err := loopbackField(block, "listen", key+".listen"); err != nil {
			return err
		}
	}
	for _, key := range []string{"ss-config", "vmess-config"} {
		if err := loopbackURL(doc, key); err != nil {
			return err
		}
	}
	return nil
}

func loopbackTunnels(doc map[string]any) error {
	raw, ok := doc["tunnels"]
	if !ok || raw == nil {
		return nil
	}
	items, ok := raw.([]any)
	if !ok {
		return errors.New("tunnels must be a list")
	}
	for i, item := range items {
		name := fmt.Sprintf("tunnels[%d]", i)
		switch tunnel := item.(type) {
		case string:
			// network,address,target[,proxy]
			parts := strings.Split(tunnel, ",")
			if len(parts) < 3 {
				return fmt.Errorf("%s is not network,address,target[,proxy]", name)
			}
			address, err := withLoopbackHost(strings.TrimSpace(parts[1]))
			if err != nil {
				return fmt.Errorf("%s: %w", name, err)
			}
			parts[1] = address
			items[i] = strings.Join(parts, ",")
		case map[string]any:
			if err := loopbackField(tunnel, "address", name+".address"); err != nil {
				return err
			}
		default:
			return fmt.Errorf("%s must be a string or a mapping", name)
		}
	}
	return nil
}

func loopbackField(block map[string]any, key string, name string) error {
	raw, ok := block[key]
	if !ok || raw == nil {
		return nil
	}
	address, ok := raw.(string)
	if !ok {
		return fmt.Errorf("%s must be a string", name)
	}
	if address == "" {
		return nil
	}
	rewritten, err := withLoopbackHost(address)
	if err != nil {
		return fmt.Errorf("%s: %w", name, err)
	}
	block[key] = rewritten
	return nil
}

func loopbackURL(doc map[string]any, key string) error {
	raw, ok := doc[key]
	if !ok || raw == nil {
		return nil
	}
	value, ok := raw.(string)
	if !ok {
		return fmt.Errorf("%s must be a string", key)
	}
	if value == "" {
		return nil
	}
	parsed, err := url.Parse(value)
	if err != nil {
		return fmt.Errorf("%s: %w", key, err)
	}
	if parsed.Host, err = withLoopbackHost(parsed.Host); err != nil {
		return fmt.Errorf("%s: %w", key, err)
	}
	doc[key] = parsed.String()
	return nil
}

func withLoopbackHost(address string) (string, error) {
	_, port, err := net.SplitHostPort(address)
	if err != nil {
		return "", err
	}
	return net.JoinHostPort(loopback, port), nil
}

// enforceSecurity is the last step before parsing, after the override, so neither the
// profile nor the override can undo it (docs/architecture.md §3.3).
func enforceSecurity(doc map[string]any) error {
	// Empty, not absent: a missing external-ui-url falls back to mihomo's download URL.
	for _, key := range controllerKeys {
		doc[key] = ""
	}
	// Node selections survive a restart and the next boot (§3.3 #5).
	section(doc, "profile")["store-selected"] = true
	// iptables is Linux-only and mihomo exits the process when it cannot apply it.
	doc["iptables"] = map[string]any{"enable": false}
	// Setting the clock needs a privilege the user may not hold; SYSTEM must not do it for them.
	section(doc, "ntp")["write-to-system"] = false
	for _, key := range []string{"proxy-providers", "rule-providers"} {
		if err := checkProviderPaths(doc, key); err != nil {
			return err
		}
	}
	return nil
}

// checkProviderPaths keeps provider files inside the home directory with the
// platform's own path rules, so `..\` cannot climb out on Windows (§3.3 #4). mihomo's
// IsSafePath stays as the backstop.
func checkProviderPaths(doc map[string]any, key string) error {
	raw, ok := doc[key]
	if !ok || raw == nil {
		return nil
	}
	providers, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("%s must be a mapping", key)
	}
	for name, value := range providers {
		provider, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s.%s must be a mapping", key, name)
		}
		rawPath, ok := provider["path"]
		if !ok || rawPath == nil {
			continue
		}
		path, ok := rawPath.(string)
		if !ok {
			return fmt.Errorf("%s.%s.path must be a string", key, name)
		}
		if path == "" {
			delete(provider, "path")
			continue
		}
		local, err := localPath(path)
		if err != nil {
			return fmt.Errorf("%s.%s.path: %w", key, name, err)
		}
		provider["path"] = local
	}
	return nil
}

func localPath(path string) (string, error) {
	cleaned := filepath.Clean(filepath.FromSlash(path))
	if !filepath.IsLocal(cleaned) {
		return "", fmt.Errorf("%q must stay inside the home directory", path)
	}
	return cleaned, nil
}

// verifyAdmitted checks the parsed configuration against what the patches promised,
// and fails closed if mihomo read something they missed.
func verifyAdmitted(cfg *config.Config, mode TunMode, options RunOptions) error {
	controller := cfg.Controller
	for name, value := range map[string]string{
		"external-controller":      controller.ExternalController,
		"external-controller-tls":  controller.ExternalControllerTLS,
		"external-controller-unix": controller.ExternalControllerUnix,
		"external-controller-pipe": controller.ExternalControllerPipe,
		"external-doh-server":      controller.ExternalDohServer,
		"secret":                   controller.Secret,
		"external-ui":              controller.ExternalUI,
		"external-ui-name":         controller.ExternalUIName,
		"external-ui-url":          controller.ExternalUIURL,
	} {
		if value != "" {
			return fmt.Errorf("%s survived admission", name)
		}
	}
	if !cfg.Profile.StoreSelected {
		return errors.New("store-selected is off")
	}
	if cfg.IPTables.Enable || cfg.NTP.WriteToSystem {
		return errors.New("iptables or ntp write-to-system survived admission")
	}
	general := cfg.General
	if general.Tun.Enable != options.TUN {
		return fmt.Errorf("tun.enable is %v, want %v", general.Tun.Enable, options.TUN)
	}
	if general.Tun.FileDescriptor != 0 && mode != TunFD {
		return errors.New("tun.file-descriptor is only for fd mode")
	}
	for name, listener := range cfg.Listeners {
		if takesOverSystem(listener) {
			return fmt.Errorf("listener %s takes over system traffic", name)
		}
	}
	if mode == TunNone && (general.Tun.AutoRoute || general.Tun.AutoRedirect ||
		general.RedirPort != 0 || general.TProxyPort != 0) {
		return errors.New("routing inbounds survived the no-TUN patch")
	}
	if options.AllowLAN {
		return nil
	}
	if general.AllowLan {
		return errors.New("allow-lan is on")
	}
	for name, listener := range cfg.Listeners {
		if err := requireLoopback(listener.RawAddress()); err != nil {
			return fmt.Errorf("listener %s: %w", name, err)
		}
	}
	for _, tunnel := range cfg.Tunnels {
		if err := requireLoopback(tunnel.Address); err != nil {
			return fmt.Errorf("tunnel %s: %w", tunnel.Address, err)
		}
	}
	if cfg.DNS.Enable && cfg.DNS.Listen != "" {
		if err := requireLoopback(cfg.DNS.Listen); err != nil {
			return fmt.Errorf("dns.listen: %w", err)
		}
	}
	if general.TuicServer.Enable {
		if err := requireLoopback(general.TuicServer.Listen); err != nil {
			return fmt.Errorf("tuic-server.listen: %w", err)
		}
	}
	return nil
}

// requireLoopback accepts host:port, or the comma-separated list a multi-port listener reports.
func requireLoopback(addresses string) error {
	for address := range strings.SplitSeq(addresses, ",") {
		host, _, err := net.SplitHostPort(address)
		if err != nil {
			return err
		}
		ip, err := netip.ParseAddr(host)
		if err != nil || !ip.IsLoopback() {
			return fmt.Errorf("%s is not a loopback address", address)
		}
	}
	return nil
}
