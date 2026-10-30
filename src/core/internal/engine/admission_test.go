package engine

import (
	"errors"
	"fmt"
	"net/netip"
	"slices"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	LC "github.com/metacubex/mihomo/listener/config"
)

func admitted(t *testing.T, mode TunMode, profile Profile) *config.Config {
	t.Helper()
	cfg, err := admit(profile, mode)
	if err != nil {
		t.Fatalf("admit: %v", err)
	}
	return cfg
}

func profileOf(source string) Profile {
	return Profile{Config: []byte(source)}
}

const minimal = "mixed-port: 7890\nproxies: []\nrules: ['MATCH,DIRECT']\n"

// The development guardrails (docs/architecture.md §11): each test pins one of them.

func TestNoTUNClearsTun(t *testing.T) {
	cfg := admitted(t, TunNone, profileOf(minimal+`
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
	cfg := admitted(t, TunNone, profileOf(minimal+"redir-port: 7892\ntproxy-port: 7893\n"))
	if cfg.General.RedirPort != 0 || cfg.General.TProxyPort != 0 {
		t.Fatalf("redir-port = %d, tproxy-port = %d, want 0", cfg.General.RedirPort, cfg.General.TProxyPort)
	}
}

func TestNoTUNDropsSystemListeners(t *testing.T) {
	cfg := admitted(t, TunNone, profileOf(minimal+`
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
	cfg := admitted(t, TunNone, profile)
	if cfg.General.AllowLan || cfg.General.BindAddress != loopback {
		t.Fatalf("allow-lan = %v, bind-address = %q", cfg.General.AllowLan, cfg.General.BindAddress)
	}
}

func TestNoTUNKeepsInboundsOnLoopback(t *testing.T) {
	cfg := admitted(t, TunNone, profileOf(minimal+`
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
	if _, err := admit(profile, TunNone); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("admit with TUN = %v, want ErrUnsupported", err)
	}
}

func TestOverrideCannotReopenWhatNoTUNCloses(t *testing.T) {
	profile := profileOf(minimal)
	profile.Overrides = [][]byte{
		[]byte("tun: {enable: true, auto-route: true, auto-redirect: true}\ntproxy-port: 7893\n"),
		[]byte("redir-port: 7892\nlisteners: [{name: tun-in, type: tun}, {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}]\n"),
	}
	cfg := admitted(t, TunNone, profile)
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
	cfg := admitted(t, TunOwned, profile)
	tun := cfg.General.Tun
	if !tun.Enable || !tun.AutoRoute || !tun.StrictRoute || tun.FileDescriptor != 0 || cfg.General.AllowLan {
		t.Fatalf("tun = %+v, allow-lan = %v", tun, cfg.General.AllowLan)
	}
}

func TestOwnedTUNWritesTheTunSection(t *testing.T) {
	profile := profileOf(minimal + "tun: {enable: false, stack: system, device: utun-md, auto-route: false, file-descriptor: 9}\n")
	profile.Options.TUN = true
	cfg := admitted(t, TunOwned, profile)
	tun := cfg.General.Tun
	if !tun.Enable || !tun.AutoRoute || !tun.AutoDetectInterface || !tun.StrictRoute || tun.AutoRedirect ||
		tun.FileDescriptor != 0 || tun.Device != "utun-md" || tun.Stack.String() != "System" {
		t.Fatalf("tun = %+v", tun)
	}
	if !cfg.DNS.Enable {
		t.Fatal("dns is off; the TUN hijacks DNS into mihomo")
	}

	profile.Options.TUN = false
	if cfg := admitted(t, TunOwned, profile); cfg.General.Tun.Enable {
		t.Fatal("tun is on although the options turn it off")
	}
}

func TestOwnedTUNRejectsAStackThisBuildLacks(t *testing.T) {
	if features.WithGVisor {
		t.Skip("this build includes gVisor")
	}
	for _, stack := range []string{"gvisor", "mixed"} {
		profile := profileOf(minimal + "tun: {stack: " + stack + "}\n")
		profile.Options.TUN = true
		if _, err := admit(profile, TunOwned); !errors.Is(err, ErrInvalidConfig) || !strings.Contains(err.Error(), "with_gvisor") {
			t.Errorf("stack %s: admit = %v", stack, err)
		}
		profile.Options.TUN = false
		if _, err := admit(profile, TunOwned); err != nil {
			t.Errorf("stack %s with the TUN off: admit = %v", stack, err)
		}
	}
}

func TestOwnedTUNFollowsAllowLAN(t *testing.T) {
	profile := profileOf(minimal + "allow-lan: true\nlisteners:\n  - {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}\n")
	cfg := admitted(t, TunOwned, profile)
	if cfg.General.AllowLan || requireLoopback(cfg.Listeners["socks-in"].RawAddress()) != nil {
		t.Fatal("inbounds left loopback without allow_lan")
	}
	profile.Options.AllowLAN = true
	cfg = admitted(t, TunOwned, profile)
	if !cfg.General.AllowLan || cfg.Listeners["socks-in"].RawAddress() != "0.0.0.0:7896" {
		t.Fatalf("allow_lan did not open: allow-lan=%v listener=%s", cfg.General.AllowLan, cfg.Listeners["socks-in"].RawAddress())
	}
}

func TestRunOptionsReplaceTheMixedPort(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.MixedPort = 17890
	if cfg := admitted(t, TunNone, profile); cfg.General.MixedPort != 17890 {
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
	cfg := admitted(t, TunNone, profile)
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

func TestOverridesExtendLists(t *testing.T) {
	doc, err := decodeDocument([]byte(`proxies: [{name: a, type: direct}]
proxy-groups: [{name: G, type: select, proxies: [a]}]
rules: ['DOMAIN,a.example,G', 'MATCH,G']
`))
	if err != nil {
		t.Fatal(err)
	}
	err = applyOverrides(doc, [][]byte{
		[]byte(`prepend-rules: ['DOMAIN,first.example,DIRECT']
append-rules: ['MATCH,DIRECT']
prepend-proxies: [{name: p0, type: direct}]
append-proxies: [{name: p9, type: direct}]
prepend-proxy-groups: [{name: G0, type: select, proxies: [p0]}]
append-proxy-groups: [{name: G9, type: select, proxies: [p9]}]
`),
		// A later override replaces the list and extends the replacement.
		[]byte("rules: ['MATCH,G']\nprepend-rules: ['DOMAIN,second.example,DIRECT']\n"),
	})
	if err != nil {
		t.Fatal(err)
	}
	names := func(key string) []string {
		var out []string
		for _, item := range doc[key].([]any) {
			out = append(out, item.(map[string]any)["name"].(string))
		}
		return out
	}
	if got := fmt.Sprint(names("proxies")); got != "[p0 a p9]" {
		t.Errorf("proxies = %s", got)
	}
	if got := fmt.Sprint(names("proxy-groups")); got != "[G0 G G9]" {
		t.Errorf("proxy-groups = %s", got)
	}
	if got := fmt.Sprint(doc["rules"]); got != "[DOMAIN,second.example,DIRECT MATCH,G]" {
		t.Errorf("rules = %s", got)
	}
	for _, key := range []string{"prepend-rules", "append-proxies"} {
		if _, ok := doc[key]; ok {
			t.Errorf("%s reached the configuration", key)
		}
	}

	profile := profileOf(minimal)
	profile.Overrides = [][]byte{[]byte("prepend-rules: ['DOMAIN,first.example,REJECT']\n")}
	cfg := admitted(t, TunNone, profile)
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
		_, err := admit(profile, TunNone)
		if !errors.Is(err, ErrInvalidConfig) || !strings.Contains(err.Error(), "override 2 of 2") {
			t.Errorf("%s: admit = %v", name, err)
		}
	}
}

func TestAdmissionRejectsBrokenInput(t *testing.T) {
	for name, profile := range map[string]Profile{
		"empty":         profileOf("  \n"),
		"not a mapping": profileOf("- a\n- b\n"),
		"bad yaml":      profileOf("proxies: [\n"),
		"unknown rule":  profileOf("rules: ['MATCH,NOWHERE']\n"),
		"bad listeners": profileOf(minimal + "listeners: {a: b}\n"),
	} {
		if _, err := admit(profile, TunNone); !errors.Is(err, ErrInvalidConfig) {
			t.Errorf("%s: admit = %v, want ErrInvalidConfig", name, err)
		}
	}
}

// fd mode (docs/architecture.md §3.1): the macOS extension owns the TUN, its addresses,
// routes and DNS; mihomo's TUN only reads and writes packets on the network FDNetwork names.

func TestFDModeWritesTheTunSection(t *testing.T) {
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
	cfg := admitted(t, TunFD, profile)
	tun := cfg.General.Tun
	if !tun.Enable || tun.AutoRoute || tun.AutoRedirect || tun.AutoDetectInterface || tun.StrictRoute ||
		tun.DNSMode != "disabled" || tun.FileDescriptor != 0 || tun.Device != "" || tun.Stack.String() != "System" {
		t.Fatalf("tun = %+v", tun)
	}
	if !slices.Equal(tun.Inet4Address, []netip.Prefix{FDNetwork.Inet4}) ||
		!slices.Equal(tun.Inet6Address, []netip.Prefix{FDNetwork.Inet6}) || tun.MTU != FDNetwork.MTU {
		t.Fatalf("tun network = %v %v mtu %d, want %+v", tun.Inet4Address, tun.Inet6Address, tun.MTU, FDNetwork)
	}
	if !cfg.DNS.Enable || cfg.DNS.FakeIPRange.String() != fdFakeIPRange {
		t.Fatalf("dns enable = %v, fake-ip-range = %s", cfg.DNS.Enable, cfg.DNS.FakeIPRange)
	}
}

func TestFDModeKeepsTheTUNOn(t *testing.T) {
	profile := profileOf(minimal)
	profile.Options.TUN = false
	if cfg := admitted(t, TunFD, profile); !cfg.General.Tun.Enable {
		t.Fatal("tun is off; the extension only runs with its tunnel up")
	}
}

func TestFDModeDefaultsTheStack(t *testing.T) {
	want := C.TunMips
	if features.WithGVisor {
		want = C.TunGvisor
	}
	if stack := admitted(t, TunFD, profileOf(minimal)).General.Tun.Stack; stack != want {
		t.Fatalf("stack = %v, want %v", stack, want)
	}
}

func TestOverrideCannotMoveTheFDNetwork(t *testing.T) {
	profile := profileOf(minimal)
	profile.Overrides = [][]byte{[]byte(`
tun: {auto-route: true, strict-route: true, mtu: 1400, inet6-address: ['fd00::1/126']}
dns: {fake-ip-range: 10.0.0.1/8}
allow-lan: true
`)}
	cfg := admitted(t, TunFD, profile)
	tun := cfg.General.Tun
	if tun.AutoRoute || tun.StrictRoute || tun.MTU != FDNetwork.MTU || cfg.General.AllowLan ||
		!slices.Equal(tun.Inet4Address, []netip.Prefix{FDNetwork.Inet4}) ||
		!slices.Equal(tun.Inet6Address, []netip.Prefix{FDNetwork.Inet6}) {
		t.Fatalf("tun = %+v, allow-lan = %v", tun, cfg.General.AllowLan)
	}
}

func TestFDModeFollowsAllowLAN(t *testing.T) {
	profile := profileOf(minimal + "listeners:\n  - {name: socks-in, type: socks, port: 7896, listen: 0.0.0.0}\n")
	cfg := admitted(t, TunFD, profile)
	if cfg.General.AllowLan || requireLoopback(cfg.Listeners["socks-in"].RawAddress()) != nil {
		t.Fatal("inbounds left loopback without allow_lan")
	}
	profile.Options.AllowLAN = true
	if cfg := admitted(t, TunFD, profile); !cfg.General.AllowLan {
		t.Fatal("allow_lan did not open")
	}
}

// The extension makes DNSServer the system's resolver; mihomo hijacks the address after
// its TUN's own, which is the same one only while the TUN sits on FDNetwork.Inet4.
func TestFDNetworkDNSIsTheHijackedAddress(t *testing.T) {
	if FDNetwork.Inet4.String() != "198.18.0.1/30" || FDNetwork.DNSServer().String() != "198.18.0.2" {
		t.Fatalf("network = %+v, dns = %s", FDNetwork, FDNetwork.DNSServer())
	}
	if err := verifyFDTun(LC.Tun{Inet4Address: []netip.Prefix{netip.MustParsePrefix("28.0.0.1/30")}, MTU: FDNetwork.MTU}); err == nil {
		t.Fatal("a TUN off FDNetwork passed")
	}
}
