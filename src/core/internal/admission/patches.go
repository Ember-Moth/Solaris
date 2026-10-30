package admission

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"strings"

	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
)

const loopback = "127.0.0.1"

// systemListenerTypes take over the system's traffic: a TUN, or redirected and
// transparent-proxy sockets that need routes or firewall rules. The TUN is only ever the
// one the tun section describes, so the pipeline drops these from listeners in every mode.
var systemListenerTypes = map[string]bool{"tun": true, "redir": true, "tproxy": true}

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
// device, MTU and exclusions stay, and so does the stack unless the options name one.
func patchOwnedTUN(doc map[string]any, options RunOptions) error {
	tun := section(doc, "tun")
	tun["enable"] = options.TUN
	tun["auto-route"] = true
	tun["auto-detect-interface"] = true
	tun["strict-route"] = true
	tun["auto-redirect"] = false
	tun["file-descriptor"] = 0
	if options.Stack != "" {
		tun["stack"] = options.Stack
	}
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

// patchFDTUN writes the TUN section for the descriptor the macOS extension owns. The
// extension routes and sets the system's DNS, so mihomo neither routes nor watches
// interfaces, and the TUN's addresses are the ones the extension gave the utun: mihomo
// takes the IPv4 address from the fake-ip range, so the range is pinned too. The engine
// fills in tun.file-descriptor when it applies the configuration.
func patchFDTUN(doc map[string]any, options RunOptions, host Host) error {
	tun := section(doc, "tun")
	tun["enable"] = true
	tun["auto-route"] = false
	tun["auto-redirect"] = false
	tun["auto-detect-interface"] = false
	tun["strict-route"] = false
	tun["dns-mode"] = "disabled"
	tun["file-descriptor"] = 0
	delete(tun, "device")
	tun["inet6-address"] = []any{FDNetwork.Inet6.String()}
	tun["mtu"] = int(FDNetwork.MTU)
	if options.Stack != "" {
		tun["stack"] = options.Stack
	}
	switch stack, _ := tun["stack"].(string); {
	case stack == "":
		tun["stack"] = defaultFDStack()
	case host.IncludeAllNetworks && breaksAllNetworks(stack):
		// mihomo does not pass the setting on to sing-tun, whose own check would refuse these.
		if host.Warn != nil {
			host.Warn(fmt.Sprintf("the %s TUN stack does not work while the tunnel includes all networks; using %s",
				stack, defaultFDStack()))
		}
		tun["stack"] = defaultFDStack()
	}
	dns := section(doc, "dns")
	dns["enable"] = true
	dns["fake-ip-range"] = fdFakeIPRange
	setMixedPort(doc, options)
	if options.AllowLAN {
		doc["allow-lan"] = true
		return nil
	}
	return restrictToLoopback(doc)
}

// defaultFDStack is gVisor when the build has it, mips otherwise: the two stacks
// NetworkExtension still allows once the tunnel includes all networks (§3.4).
func defaultFDStack() string {
	if features.WithGVisor {
		return "gvisor"
	}
	return "mips"
}

// breaksAllNetworks reports the stacks that stop working once a NetworkExtension tunnel
// includes all networks: system and mixed, which hand TCP to the system's own stack.
func breaksAllNetworks(stack string) bool {
	return strings.EqualFold(stack, C.TunSystem.String()) || strings.EqualFold(stack, C.TunMixed.String())
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
