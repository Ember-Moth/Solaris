package admission

import (
	"errors"
	"fmt"
	"net"
	"net/netip"
	"slices"
	"strings"

	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	LC "github.com/metacubex/mihomo/listener/config"
	"github.com/metacubex/mihomo/listener/inbound"
)

// Verify checks the configuration mihomo parsed from prepared.YAML against what the patches
// promised, and fails closed if mihomo read something they missed. Its errors wrap
// ErrInvalidConfig.
func Verify(cfg *config.Config, prepared *Prepared) error {
	if err := verify(cfg, prepared.Host, prepared.Options); err != nil {
		return invalidConfig(err)
	}
	return nil
}

func verify(cfg *config.Config, host Host, options RunOptions) error {
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
	mode := host.Tun
	general := cfg.General
	if general.Tun.Enable != options.TUN {
		return fmt.Errorf("tun.enable is %v, want %v", general.Tun.Enable, options.TUN)
	}
	if general.Tun.FileDescriptor != 0 && mode != TunFD {
		return errors.New("tun.file-descriptor is only for fd mode")
	}
	if mode == TunFD {
		if err := verifyFDTun(general.Tun, host.IncludeAllNetworks); err != nil {
			return err
		}
	}
	// Without with_gvisor these stacks only fail once mihomo opens the TUN.
	if stack := general.Tun.Stack; general.Tun.Enable && !features.WithGVisor &&
		(stack == C.TunGvisor || stack == C.TunMixed) {
		return fmt.Errorf("tun.stack %s needs a build with the with_gvisor tag", stack)
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

func takesOverSystem(listener C.InboundListener) bool {
	switch listener.(type) {
	case *inbound.Tun, *inbound.Redir, *inbound.TProxy:
		return true
	}
	return false
}

// verifyFDTun checks that mihomo's TUN matches the network the extension set up, leaves
// routing and the system's DNS to it, and runs a stack its tunnel allows.
func verifyFDTun(tun LC.Tun, allNetworks bool) error {
	if tun.AutoRoute || tun.AutoRedirect || tun.AutoDetectInterface || tun.StrictRoute {
		return errors.New("fd mode leaves routing to the host")
	}
	if allNetworks && (tun.Stack == C.TunSystem || tun.Stack == C.TunMixed) {
		return fmt.Errorf("tun.stack %s does not work while the tunnel includes all networks", tun.Stack)
	}
	if !slices.Equal(tun.Inet4Address, []netip.Prefix{FDNetwork.Inet4}) {
		return fmt.Errorf("the TUN's IPv4 address is %v, want %s", tun.Inet4Address, FDNetwork.Inet4)
	}
	// mihomo drops the IPv6 address when ipv6 is off; the extension still routes IPv6
	// into the tunnel, where it goes nowhere.
	if len(tun.Inet6Address) != 0 && !slices.Equal(tun.Inet6Address, []netip.Prefix{FDNetwork.Inet6}) {
		return fmt.Errorf("the TUN's IPv6 address is %v, want %s", tun.Inet6Address, FDNetwork.Inet6)
	}
	if tun.MTU != FDNetwork.MTU {
		return fmt.Errorf("the TUN's MTU is %d, want %d", tun.MTU, FDNetwork.MTU)
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
