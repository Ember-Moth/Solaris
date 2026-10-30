package engine

// The admission pipeline end to end, through mihomo's parser: these tests check the
// configuration mihomo read. internal/admission tests the steps that need no parser.

import (
	"errors"
	"net"
	"net/netip"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// admit runs profile through the pipeline as an engine of mode would.
func admit(profile admission.Profile, mode admission.TunMode) (*config.Config, error) {
	result, err := admitProfile(profile, admission.Host{Tun: mode})
	if err != nil {
		return nil, err
	}
	return result.cfg, nil
}

func admitted(t *testing.T, mode admission.TunMode, profile admission.Profile) *config.Config {
	t.Helper()
	cfg, err := admit(profile, mode)
	if err != nil {
		t.Fatalf("admit: %v", err)
	}
	return cfg
}

func profileOf(source string) admission.Profile {
	return admission.Profile{Config: []byte(source)}
}

// requireLoopback accepts host:port, or the comma-separated list a multi-port listener
// reports, when the host is a loopback address.
func requireLoopback(addresses string) error {
	for address := range strings.SplitSeq(addresses, ",") {
		host, _, err := net.SplitHostPort(address)
		if err != nil {
			return err
		}
		if ip, err := netip.ParseAddr(host); err != nil || !ip.IsLoopback() {
			return errors.New(address + " is not a loopback address")
		}
	}
	return nil
}

const minimal = "mixed-port: 7890\nproxies: []\nrules: ['MATCH,DIRECT']\n"

// The development guardrails (docs/architecture.md §11): each test pins one of them.

func TestNoTUNClearsTun(t *testing.T) {
	cfg := admitted(t, admission.TunNone, profileOf(minimal+`
tun:
  enable: true
  stack: system
  auto-route: true
  auto-redirect: true
  auto-detect-interface: true
  strict-route: true
`))
	tun := cfg.General.Tun
	if tun.Enable || tun.AutoRoute || tun.AutoRedirect || tun.AutoDetectInterface || tun.StrictRoute {
		t.Fatalf("tun = %+v, want everything off", tun)
	}
}

func TestNoTUNClearsRedirAndTProxyPorts(t *testing.T) {
	cfg := admitted(t, admission.TunNone, profileOf(minimal+"redir-port: 7892\ntproxy-port: 7893\n"))
	if cfg.General.RedirPort != 0 || cfg.General.TProxyPort != 0 {
		t.Fatalf("redir-port = %d, tproxy-port = %d, want 0", cfg.General.RedirPort, cfg.General.TProxyPort)
	}
}

func TestNoTUNDropsSystemListeners(t *testing.T) {
	cfg := admitted(t, admission.TunNone, profileOf(minimal+`
listeners:
  - {name: tun-in, type: tun, stack: system, auto-route: true}
  - {name: redir-in, type: redir, port: 7894}
  - {name: tproxy-in, type: tproxy, port: 7895}
  - {name: socks-in, type: socks, port: 7896}
`))
	if len(cfg.Listeners) != 1 || cfg.Listeners["socks-in"] == nil {
		t.Fatalf("listeners = %v, want only socks-in", cfg.Listeners)
	}
}

func TestNoTUNForcesAllowLANOff(t *testing.T) {
	profile := profileOf(minimal + "allow-lan: true\nbind-address: '*'\n")
	profile.Overrides = [][]byte{[]byte("allow-lan: true\nbind-address: 0.0.0.0\n")}
	profile.Options.AllowLAN = true
	cfg := admitted(t, admission.TunNone, profile)
	if cfg.General.AllowLan || cfg.General.BindAddress != "127.0.0.1" {
		t.Fatalf("allow-lan = %v, bind-address = %q", cfg.General.AllowLan, cfg.General.BindAddress)
	}
}

func TestNoTUNKeepsInboundsOnLoopback(t *testing.T) {
	cfg := admitted(t, admission.TunNone, profileOf(minimal+`
listeners:
  - {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}
  - {name: mixed-in, type: mixed, port: 7897}
tunnels:
  - tcp/udp,0.0.0.0:6553,1.1.1.1:53,DIRECT
  - {network: [tcp], address: "[::]:6554", target: "1.1.1.1:53"}
dns:
  enable: true
  listen: 0.0.0.0:1053
`))
	for name, listener := range cfg.Listeners {
		if err := requireLoopback(listener.RawAddress()); err != nil {
			t.Errorf("listener %s: %v", name, err)
		}
	}
	for _, tunnel := range cfg.Tunnels {
		if err := requireLoopback(tunnel.Address); err != nil {
			t.Errorf("tunnel: %v", err)
		}
	}
	if cfg.DNS.Listen != "127.0.0.1:1053" {
		t.Errorf("dns.listen = %q", cfg.DNS.Listen)
	}
}

func TestNoTUNRejectsTheTUNOption(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.TUN = true
	if _, err := admit(profile, admission.TunNone); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("admit with TUN = %v, want ErrUnsupported", err)
	}
}

func TestOverrideCannotReopenWhatNoTUNCloses(t *testing.T) {
	profile := profileOf(minimal)
	profile.Overrides = [][]byte{
		[]byte("tun: {enable: true, auto-route: true, auto-redirect: true}\ntproxy-port: 7893\n"),
		[]byte("redir-port: 7892\nlisteners: [{name: tun-in, type: tun}, {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}]\n"),
	}
	cfg := admitted(t, admission.TunNone, profile)
	general := cfg.General
	if general.Tun.Enable || general.Tun.AutoRoute || general.Tun.AutoRedirect ||
		general.TProxyPort != 0 || general.RedirPort != 0 || cfg.Listeners["tun-in"] != nil {
		t.Fatalf("an override reopened: tun=%+v tproxy=%d redir=%d listeners=%v",
			general.Tun, general.TProxyPort, general.RedirPort, cfg.Listeners)
	}
	if err := requireLoopback(cfg.Listeners["socks-in"].RawAddress()); err != nil {
		t.Fatalf("an override moved a listener off loopback: %v", err)
	}
}

func TestOverrideCannotLoosenOwnedTUN(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.TUN = true
	profile.Overrides = [][]byte{[]byte("tun: {enable: false, auto-route: false, strict-route: false, file-descriptor: 7}\nallow-lan: true\n")}
	cfg := admitted(t, admission.TunOwned, profile)
	tun := cfg.General.Tun
	if !tun.Enable || !tun.AutoRoute || !tun.StrictRoute || tun.FileDescriptor != 0 || cfg.General.AllowLan {
		t.Fatalf("tun = %+v, allow-lan = %v", tun, cfg.General.AllowLan)
	}
}

func TestOwnedTUNWritesTheTunSection(t *testing.T) {
	profile := profileOf(minimal + "tun: {enable: false, stack: system, device: utun-md, auto-route: false, file-descriptor: 9}\n")
	profile.Options.TUN = true
	cfg := admitted(t, admission.TunOwned, profile)
	tun := cfg.General.Tun
	if !tun.Enable || !tun.AutoRoute || !tun.AutoDetectInterface || !tun.StrictRoute || tun.AutoRedirect ||
		tun.FileDescriptor != 0 || tun.Device != "utun-md" || tun.Stack.String() != "System" {
		t.Fatalf("tun = %+v", tun)
	}
	if !cfg.DNS.Enable {
		t.Fatal("dns is off; the TUN hijacks DNS into mihomo")
	}

	profile.Options.TUN = false
	if cfg := admitted(t, admission.TunOwned, profile); cfg.General.Tun.Enable {
		t.Fatal("tun is on although the options turn it off")
	}
}

func TestOwnedTUNRejectsAStackThisBuildLacks(t *testing.T) {
	if features.WithGVisor {
		t.Skip("this build includes gVisor")
	}
	for _, stack := range []string{"gvisor", "mixed"} {
		fromProfile := profileOf(minimal + "tun: {stack: " + stack + "}\n")
		fromOptions := profileOf(minimal)
		fromOptions.Options.Stack = stack
		for _, profile := range []admission.Profile{fromProfile, fromOptions} {
			profile.Options.TUN = true
			if _, err := admit(profile, admission.TunOwned); !errors.Is(err, ErrInvalidConfig) || !strings.Contains(err.Error(), "with_gvisor") {
				t.Errorf("stack %s: admit = %v", stack, err)
			}
			profile.Options.TUN = false
			if _, err := admit(profile, admission.TunOwned); err != nil {
				t.Errorf("stack %s with the TUN off: admit = %v", stack, err)
			}
		}
	}
}

// The settings page's stack (D3) wins over the profile and the overrides: the patches
// come after them.
func TestRunOptionsChooseTheStack(t *testing.T) {
	profile := profileOf(minimal + "tun: {stack: mips}\n")
	profile.Overrides = [][]byte{[]byte("tun: {stack: mips}\n")}
	profile.Options = admission.RunOptions{TUN: true, Stack: "system"}
	for _, mode := range []admission.TunMode{admission.TunOwned, admission.TunFD} {
		if stack := admitted(t, mode, profile).General.Tun.Stack; stack != C.TunSystem {
			t.Errorf("%v: stack = %v, want System", mode, stack)
		}
	}
	profile.Options.Stack = ""
	for _, mode := range []admission.TunMode{admission.TunOwned, admission.TunFD} {
		if stack := admitted(t, mode, profile).General.Tun.Stack; stack != C.TunMips {
			t.Errorf("%v without a stack in the options: stack = %v, want the profile's Mips", mode, stack)
		}
	}
}

func TestOwnedTUNFollowsAllowLAN(t *testing.T) {
	profile := profileOf(minimal + "allow-lan: true\nlisteners:\n  - {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}\n")
	cfg := admitted(t, admission.TunOwned, profile)
	if cfg.General.AllowLan || requireLoopback(cfg.Listeners["socks-in"].RawAddress()) != nil {
		t.Fatal("inbounds left loopback without allow_lan")
	}
	profile.Options.AllowLAN = true
	cfg = admitted(t, admission.TunOwned, profile)
	if !cfg.General.AllowLan || cfg.Listeners["socks-in"].RawAddress() != "0.0.0.0:7896" {
		t.Fatalf("allow_lan did not open: allow-lan=%v listener=%s", cfg.General.AllowLan, cfg.Listeners["socks-in"].RawAddress())
	}
}

func TestRunOptionsReplaceTheMixedPort(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.MixedPort = 17890
	if cfg := admitted(t, admission.TunNone, profile); cfg.General.MixedPort != 17890 {
		t.Fatalf("mixed-port = %d", cfg.General.MixedPort)
	}
}

func TestOverridesMergeInOrder(t *testing.T) {
	profile := profileOf(minimal + `mode: global
log-level: debug
sniffer: {enable: false, force-domain: [a.example], sniff: {HTTP: {ports: [80]}}}
dns: {enable: true, ipv6: true, nameserver: [1.1.1.1, 8.8.8.8]}
`)
	profile.Overrides = [][]byte{
		// The global override comes first ...
		[]byte("mode: direct\nsniffer: {enable: true}\ndns: {nameserver: [9.9.9.9]}\nsocks-port: 7891\n"),
		// ... and the profile's own override wins over it.
		[]byte("mode: rule\nsniffer: {force-domain: [b.example]}\n"),
		[]byte("# only a comment\n"),
		nil,
	}
	cfg := admitted(t, admission.TunNone, profile)
	if cfg.General.Mode.String() != "rule" || cfg.General.LogLevel.String() != "debug" || cfg.General.SocksPort != 7891 {
		t.Errorf("mode=%s log-level=%s socks-port=%d", cfg.General.Mode, cfg.General.LogLevel, cfg.General.SocksPort)
	}
	// Mappings merge key by key; lists replace whole.
	if !cfg.Sniffer.Enable || len(cfg.Sniffer.Sniffers) == 0 {
		t.Errorf("sniffer = %+v; the mappings did not merge", cfg.Sniffer)
	}
	if !cfg.DNS.IPv6 || len(cfg.DNS.NameServer) != 1 || cfg.DNS.NameServer[0].Addr != "9.9.9.9:53" {
		t.Errorf("dns ipv6=%v nameservers=%v", cfg.DNS.IPv6, cfg.DNS.NameServer)
	}
}

// The lists an override extends reach mihomo; internal/admission's test of the same name
// checks every insert key on the document.
func TestOverridesExtendLists(t *testing.T) {
	profile := profileOf(minimal)
	profile.Overrides = [][]byte{[]byte("prepend-rules: ['DOMAIN,first.example,REJECT']\n")}
	cfg := admitted(t, admission.TunNone, profile)
	if len(cfg.Rules) != 2 || cfg.Rules[0].Payload() != "first.example" {
		t.Fatalf("rules = %v", cfg.Rules)
	}
}

func TestBrokenOverrideNamesItsPosition(t *testing.T) {
	for name, override := range map[string]string{
		"bad yaml":       "mode: [\n",
		"not a mapping":  "- a\n",
		"insert no list": "append-rules: MATCH,DIRECT\n",
		"target no list": "proxies: {a: b}\nappend-proxies: [{name: x, type: direct}]\n",
	} {
		profile := profileOf(minimal)
		profile.Overrides = [][]byte{[]byte("mode: global\n"), []byte(override)}
		_, err := admit(profile, admission.TunNone)
		if !errors.Is(err, ErrInvalidConfig) || !strings.Contains(err.Error(), "override 2 of 2") {
			t.Errorf("%s: admit = %v", name, err)
		}
	}
}

func TestAdmissionRejectsBrokenInput(t *testing.T) {
	for name, profile := range map[string]admission.Profile{
		"empty":         profileOf("  \n"),
		"not a mapping": profileOf("- a\n- b\n"),
		"bad yaml":      profileOf("proxies: [\n"),
		"unknown rule":  profileOf("rules: ['MATCH,NOWHERE']\n"),
		"bad listeners": profileOf(minimal + "listeners: {a: b}\n"),
	} {
		if _, err := admit(profile, admission.TunNone); !errors.Is(err, ErrInvalidConfig) {
			t.Errorf("%s: admit = %v, want ErrInvalidConfig", name, err)
		}
	}
}

// fd mode (docs/architecture.md §3.1): the macOS extension owns the TUN, its addresses,
// routes and DNS; mihomo's TUN only reads and writes packets on the network
// admission.FDNetwork names.

// extensionHasIPv6 stands in for the utun's own address. mihomo gives the TUN an IPv6
// address only when the system has a global one; in the extension the utun carries
// admission.FDNetwork.Inet6, while CI runners have no IPv6 at all.
func extensionHasIPv6(t *testing.T) {
	t.Setenv("SKIP_SYSTEM_IPV6_CHECK", "true")
}

func TestFDModeWritesTheTunSection(t *testing.T) {
	extensionHasIPv6(t)
	profile := profileOf(minimal + `
tun:
  enable: false
  stack: system
  device: utun9
  auto-route: true
  auto-redirect: true
  auto-detect-interface: true
  strict-route: true
  dns-mode: hijack
  file-descriptor: 7
  inet4-address: [10.9.0.1/30]
  inet6-address: ['fd00::1/126']
  mtu: 1500
dns:
  enable: false
  fake-ip-range: 28.0.0.1/8
`)
	cfg := admitted(t, admission.TunFD, profile)
	tun := cfg.General.Tun
	if !tun.Enable || tun.AutoRoute || tun.AutoRedirect || tun.AutoDetectInterface || tun.StrictRoute ||
		tun.DNSMode != "disabled" || tun.FileDescriptor != 0 || tun.Device != "" || tun.Stack.String() != "System" {
		t.Fatalf("tun = %+v", tun)
	}
	if !slices.Equal(tun.Inet4Address, []netip.Prefix{admission.FDNetwork.Inet4}) ||
		!slices.Equal(tun.Inet6Address, []netip.Prefix{admission.FDNetwork.Inet6}) || tun.MTU != admission.FDNetwork.MTU {
		t.Fatalf("tun network = %v %v mtu %d, want %+v", tun.Inet4Address, tun.Inet6Address, tun.MTU, admission.FDNetwork)
	}
	if !cfg.DNS.Enable || cfg.DNS.FakeIPRange.String() != "198.18.0.1/16" {
		t.Fatalf("dns enable = %v, fake-ip-range = %s", cfg.DNS.Enable, cfg.DNS.FakeIPRange)
	}
}

func TestFDModeKeepsTheTUNOn(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.TUN = false
	if cfg := admitted(t, admission.TunFD, profile); !cfg.General.Tun.Enable {
		t.Fatal("tun is off; the extension only runs with its tunnel up")
	}
}

func TestFDModeDefaultsTheStack(t *testing.T) {
	want := C.TunMips
	if features.WithGVisor {
		want = C.TunGvisor
	}
	if stack := admitted(t, admission.TunFD, profileOf(minimal)).General.Tun.Stack; stack != want {
		t.Fatalf("stack = %v, want %v", stack, want)
	}
}

func TestFDModeRejectsAStackThisBuildLacks(t *testing.T) {
	if features.WithGVisor {
		t.Skip("this build includes gVisor")
	}
	for _, stack := range []string{"gvisor", "mixed"} {
		profile := profileOf(minimal)
		profile.Options.Stack = stack
		if _, err := admit(profile, admission.TunFD); !errors.Is(err, ErrInvalidConfig) || !strings.Contains(err.Error(), "with_gvisor") {
			t.Errorf("stack %s: admit = %v", stack, err)
		}
	}
}

// D19: NetworkExtension breaks the system and mixed stacks once the extension's tunnel
// includes all networks, so fd mode runs gVisor instead, or mips in a build without it.
func TestFDModeReplacesStacksThatBreakAllNetworks(t *testing.T) {
	want := C.TunMips
	if features.WithGVisor {
		want = C.TunGvisor
	}
	// stack admits profile on a host of mode whose tunnel includes all networks.
	stack := func(mode admission.TunMode, profile admission.Profile) C.TUNStack {
		t.Helper()
		admitted, err := admitProfile(profile, admission.Host{Tun: mode, IncludeAllNetworks: true})
		if err != nil {
			t.Fatalf("admit: %v", err)
		}
		return admitted.cfg.General.Tun.Stack
	}
	for _, name := range []string{"system", "System", "mixed"} {
		fromProfile := profileOf(minimal + "tun: {stack: " + name + "}\n")
		fromOptions := profileOf(minimal)
		fromOptions.Options.Stack = strings.ToLower(name)
		for _, profile := range []admission.Profile{fromProfile, fromOptions} {
			if got := stack(admission.TunFD, profile); got != want {
				t.Errorf("%s (options %q): stack = %v, want %v", name, profile.Options.Stack, got, want)
			}
		}
	}
	if got := stack(admission.TunFD, profileOf(minimal+"tun: {stack: mips}\n")); got != C.TunMips {
		t.Errorf("mips works there, yet became %v", got)
	}
	// Without the setting the user's stack stays, and outside fd mode it means nothing.
	system := profileOf(minimal + "tun: {stack: system}\n")
	if got := admitted(t, admission.TunFD, system).General.Tun.Stack; got != C.TunSystem {
		t.Errorf("without includeAllNetworks: stack = %v, want System", got)
	}
	system.Options = admission.RunOptions{TUN: true}
	if got := stack(admission.TunOwned, system); got != C.TunSystem {
		t.Errorf("owned TUN: stack = %v, want System", got)
	}
}

// The engine logs what the pipeline replaced, with its own prefix: here the fd stack.
func TestAdmissionLogsTheReplacedStack(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	var mu sync.Mutex
	var lines []string
	stop := engine.WatchLogs(func(entry *controlpb.LogEntry) {
		mu.Lock()
		lines = append(lines, entry.GetMessage())
		mu.Unlock()
	})
	defer stop()
	host := admission.Host{Tun: admission.TunFD, IncludeAllNetworks: true}
	if _, err := admitProfile(profileOf(minimal+"tun: {stack: system}\n"), host); err != nil {
		t.Fatal(err)
	}
	fallback := "mips"
	if features.WithGVisor {
		fallback = "gvisor"
	}
	want := "[engine] the system TUN stack does not work while the tunnel includes all networks; using " + fallback
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		mu.Lock()
		logged := slices.Contains(lines, want)
		joined := strings.Join(lines, "\n")
		mu.Unlock()
		if logged {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("no %q; got:\n%s", want, joined)
		}
	}
}

func TestOverrideCannotMoveTheFDNetwork(t *testing.T) {
	extensionHasIPv6(t)
	profile := profileOf(minimal)
	profile.Overrides = [][]byte{[]byte(`
tun: {auto-route: true, strict-route: true, mtu: 1400, inet6-address: ['fd00::1/126']}
dns: {fake-ip-range: 10.0.0.1/8}
allow-lan: true
`)}
	cfg := admitted(t, admission.TunFD, profile)
	tun := cfg.General.Tun
	if tun.AutoRoute || tun.StrictRoute || tun.MTU != admission.FDNetwork.MTU || cfg.General.AllowLan ||
		!slices.Equal(tun.Inet4Address, []netip.Prefix{admission.FDNetwork.Inet4}) ||
		!slices.Equal(tun.Inet6Address, []netip.Prefix{admission.FDNetwork.Inet6}) {
		t.Fatalf("tun = %+v, allow-lan = %v", tun, cfg.General.AllowLan)
	}
}

func TestFDModeFollowsAllowLAN(t *testing.T) {
	profile := profileOf(minimal + "listeners:\n  - {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}\n")
	cfg := admitted(t, admission.TunFD, profile)
	if cfg.General.AllowLan || requireLoopback(cfg.Listeners["socks-in"].RawAddress()) != nil {
		t.Fatal("inbounds left loopback without allow_lan")
	}
	profile.Options.AllowLAN = true
	if cfg := admitted(t, admission.TunFD, profile); !cfg.General.AllowLan {
		t.Fatal("allow_lan did not open")
	}
}
