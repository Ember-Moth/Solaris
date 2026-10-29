package engine

import (
	"errors"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/constant/features"
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
	profile.Override = []byte(`{"allow-lan": true}`)
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
	profile.Override = []byte(`{"tun": {"enable": true, "auto-route": true}, "tproxy-port": 7893,
		"listeners": [{"name": "tun-in", "type": "tun"}]}`)
	cfg := admitted(t, TunNone, profile)
	if cfg.General.Tun.Enable || cfg.General.Tun.AutoRoute || cfg.General.TProxyPort != 0 || len(cfg.Listeners) != 0 {
		t.Fatalf("override reopened: tun=%+v tproxy=%d listeners=%v", cfg.General.Tun, cfg.General.TProxyPort, cfg.Listeners)
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

func TestOverrideMergesAndRemoves(t *testing.T) {
	profile := profileOf(minimal + "mode: global\nlog-level: debug\nsniffer: {enable: true, sniff: {HTTP: {ports: [80]}}}\n")
	profile.Override = []byte(`{"mode": "direct", "log-level": null, "sniffer": {"enable": false}, "socks-port": 7891}`)
	cfg := admitted(t, TunNone, profile)
	if cfg.General.Mode.String() != "direct" || cfg.General.LogLevel.String() != "info" ||
		cfg.Sniffer.Enable || cfg.General.SocksPort != 7891 {
		t.Fatalf("mode=%s log-level=%s sniffer=%v socks-port=%d",
			cfg.General.Mode, cfg.General.LogLevel, cfg.Sniffer.Enable, cfg.General.SocksPort)
	}
}

func TestAdmissionRejectsBrokenInput(t *testing.T) {
	for name, profile := range map[string]Profile{
		"empty":         profileOf("  \n"),
		"not a mapping": profileOf("- a\n- b\n"),
		"bad yaml":      profileOf("proxies: [\n"),
		"unknown rule":  profileOf("rules: ['MATCH,NOWHERE']\n"),
		"bad override":  {Config: []byte(minimal), Override: []byte(`["not", "an", "object"]`)},
		"bad listeners": profileOf(minimal + "listeners: {a: b}\n"),
	} {
		if _, err := admit(profile, TunNone); !errors.Is(err, ErrInvalidConfig) {
			t.Errorf("%s: admit = %v, want ErrInvalidConfig", name, err)
		}
	}
}

func TestFDModeIsReserved(t *testing.T) {
	if _, err := admit(profileOf(minimal), TunFD); !errors.Is(err, ErrUnsupported) ||
		!strings.Contains(err.Error(), "M5") {
		t.Fatalf("admit in fd mode = %v", err)
	}
}
