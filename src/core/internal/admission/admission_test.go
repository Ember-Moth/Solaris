package admission

// These tests check the pipeline's steps on their own, without mihomo's parser. The tests
// of what mihomo reads from the result run the whole pipeline in internal/engine
// (admission_test.go, security_test.go).

import (
	"fmt"
	"net/netip"
	"slices"
	"testing"

	"github.com/metacubex/mihomo/common/yaml"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	LC "github.com/metacubex/mihomo/listener/config"
)

const minimal = "mixed-port: 7890\nproxies: []\nrules: ['MATCH,DIRECT']\n"

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
}

// What goes to the parser is the document, and the options are the ones the host's mode
// left; a profile's own YAML brings no composition and no mesh networks.
func TestPreparedIsWhatGoesToTheParser(t *testing.T) {
	profile := Profile{Config: []byte(minimal), Options: RunOptions{TUN: true, AllowLAN: true, MixedPort: 17890}}
	for mode, want := range map[TunMode]RunOptions{
		TunOwned: {TUN: true, AllowLAN: true, MixedPort: 17890},
		TunFD:    {TUN: true, AllowLAN: true, MixedPort: 17890},
	} {
		prepared, err := Prepare(profile, Host{Tun: mode})
		if err != nil {
			t.Fatalf("%v: %v", mode, err)
		}
		if prepared.Options != want || prepared.Host.Tun != mode {
			t.Errorf("%v: options = %+v, host = %+v", mode, prepared.Options, prepared.Host)
		}
		var decoded map[string]any
		if err := yaml.Unmarshal(prepared.YAML, &decoded); err != nil {
			t.Fatal(err)
		}
		if fmt.Sprint(decoded) != fmt.Sprint(prepared.Document) {
			t.Errorf("%v: the YAML is not the document:\n%s", mode, prepared.YAML)
		}
		if prepared.Composed != nil || prepared.Networks != nil || prepared.Warnings != nil {
			t.Errorf("%v: composed %v, networks %v, warnings %v", mode, prepared.Composed, prepared.Networks, prepared.Warnings)
		}
	}
	profile.Options.TUN = false
	prepared, err := Prepare(profile, Host{Tun: TunNone})
	if err != nil {
		t.Fatal(err)
	}
	if want := (RunOptions{MixedPort: 17890}); prepared.Options != want {
		t.Errorf("no TUN: options = %+v, want %+v", prepared.Options, want)
	}
}

// D19: replacing the stack is a warning for the engine to log; mihomo's runtime, and its
// log, are not this package's.
func TestFDModeWarnsWhenItReplacesTheStack(t *testing.T) {
	fallback := "mips"
	if features.WithGVisor {
		fallback = "gvisor"
	}
	profile := Profile{Config: []byte(minimal + "tun: {stack: system}\n")}
	prepared, err := Prepare(profile, Host{Tun: TunFD, IncludeAllNetworks: true})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"the system TUN stack does not work while the tunnel includes all networks; using " + fallback}
	if !slices.Equal(prepared.Warnings, want) {
		t.Fatalf("warnings = %q, want %q", prepared.Warnings, want)
	}
	if stack := prepared.Document["tun"].(map[string]any)["stack"]; stack != fallback {
		t.Fatalf("tun.stack = %v, want %s", stack, fallback)
	}
	mips := Profile{Config: []byte(minimal + "tun: {stack: mips}\n")}
	for name, run := range map[string]struct {
		profile Profile
		host    Host
	}{
		"without includeAllNetworks": {profile, Host{Tun: TunFD}},
		"owned TUN":                  {profile, Host{Tun: TunOwned, IncludeAllNetworks: true}},
		"mips":                       {mips, Host{Tun: TunFD, IncludeAllNetworks: true}},
	} {
		prepared, err := Prepare(run.profile, run.host)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if prepared.Warnings != nil {
			t.Errorf("%s: warnings = %q", name, prepared.Warnings)
		}
	}
}

// The extension makes DNSServer the system's resolver; mihomo hijacks the address after
// its TUN's own, which is the same one only while the TUN sits on FDNetwork.Inet4.
func TestFDNetworkDNSIsTheHijackedAddress(t *testing.T) {
	if FDNetwork.Inet4.String() != "198.18.0.1/30" || FDNetwork.DNSServer().String() != "198.18.0.2" {
		t.Fatalf("network = %+v, dns = %s", FDNetwork, FDNetwork.DNSServer())
	}
	if err := verifyFDTun(LC.Tun{Inet4Address: []netip.Prefix{netip.MustParsePrefix("28.0.0.1/30")}, MTU: FDNetwork.MTU}, false); err == nil {
		t.Fatal("a TUN off FDNetwork passed")
	}
}

func TestFDModeVerifiesTheStackForAllNetworks(t *testing.T) {
	tun := LC.Tun{Stack: C.TunMixed, Inet4Address: []netip.Prefix{FDNetwork.Inet4}, MTU: FDNetwork.MTU}
	if err := verifyFDTun(tun, false); err != nil {
		t.Fatalf("mixed without includeAllNetworks: %v", err)
	}
	if err := verifyFDTun(tun, true); err == nil {
		t.Fatal("mixed passed while the tunnel includes all networks")
	}
}
