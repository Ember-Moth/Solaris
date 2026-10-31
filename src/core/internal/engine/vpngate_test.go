package engine

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync/atomic"
	"testing"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
	"github.com/Ember-Moth/Solaris/src/core/internal/vpngate"
	"github.com/Ember-Moth/Solaris/src/core/internal/vpngate/vpngatetest"
)

// fakeVpnGateList serves a list of two servers on loopback and counts the downloads.
func fakeVpnGateList(t *testing.T) *atomic.Int32 {
	t.Helper()
	var calls atomic.Int32
	list := vpngatetest.List(
		vpngatetest.Server{Host: "vpn1", IP: "150.95.10.1", Score: 200, Country: "JP"},
		vpngatetest.Server{Host: "vpn2", IP: "150.95.10.2", Score: 100, Country: "KR"},
	)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = w.Write(list)
	}))
	t.Cleanup(server.Close)
	previous := vpnGateAPI
	vpnGateAPI = server.URL + "/api/iphone/"
	t.Cleanup(func() { vpnGateAPI = previous })
	return &calls
}

// The list goes out through the group it names: a group that rejects stops it.
func TestListVpnGateGoesThroughTheGroup(t *testing.T) {
	engine := openEngine(t, TunNone)
	calls := fakeVpnGateList(t)
	if _, err := engine.ListVpnGate(context.Background(), "VIA"); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("a stopped core: %v", err)
	}

	source := fmt.Sprintf(`mixed-port: %d
proxies:
  - {name: direct-a, type: direct}
proxy-groups:
  - {name: VIA, type: select, proxies: [direct-a]}
  - {name: BLOCKED, type: select, proxies: [REJECT]}
rules:
  - MATCH,VIA
`, porttest.Free(t))
	if _, err := engine.Start(Profile{Config: []byte(source)}); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()

	servers, err := engine.ListVpnGate(context.Background(), "VIA")
	if err != nil {
		t.Fatal(err)
	}
	if len(servers) != 2 || servers[0].GetHostName() != "vpn1" || calls.Load() != 1 {
		t.Fatalf("servers %v after %d calls", servers, calls.Load())
	}

	if _, err := engine.ListVpnGate(context.Background(), "BLOCKED"); err == nil || calls.Load() != 1 {
		t.Fatalf("through a rejecting group: %v, %d calls", err, calls.Load())
	}
	if _, err := engine.ListVpnGate(context.Background(), "NOPE"); !errors.Is(err, ErrInvalidArgument) ||
		!strings.Contains(err.Error(), "NOPE") {
		t.Fatalf("an unknown group: %v", err)
	}
}

const vpnGateRulePlan = `{"version": 1, "rules": [
	{"id": "ip", "items": [{"type": "DOMAIN-SUFFIX", "value": "ifconfig.me"}], "target": "egress:vpngate"}],
	"egress": {"vpngate": {"via": "region:HK", "mode": "%s"}}}`

// The pinned mihomo builds the servers and the group the composition writes from what
// ListVpnGate reads, in both ways of sharing out the sites.
func TestVpnGateExitIsWhatMihomoBuilds(t *testing.T) {
	var servers []*controlpb.VpnGateServer
	for i, host := range []string{"vpn1", "vpn2"} {
		options, err := vpngate.Convert(vpngatetest.Config(fmt.Sprintf("150.95.10.%d", i+1), 443+i))
		if err != nil {
			t.Fatal(err)
		}
		servers = append(servers, &controlpb.VpnGateServer{HostName: host, Country: "JP", Openvpn: options})
	}
	for mode, want := range map[string]C.AdapterType{"per-site": C.LoadBalance, "single": C.Fallback} {
		composed := composition(fmt.Sprintf(vpnGateRulePlan, mode))
		composed.VpnGate = servers
		admitted, err := admitProfile(Profile{Composition: composed}, TunNone)
		if err != nil {
			t.Fatalf("%s: %v", mode, err)
		}
		for _, name := range []string{"VPN Gate JP vpn1", "VPN Gate JP vpn2"} {
			if proxy, ok := admitted.cfg.Proxies[name]; !ok || proxy.Type() != C.OpenVPN {
				t.Fatalf("%s: %s is %v", mode, name, proxy)
			}
		}
		exit := admitted.cfg.Proxies["VPN Gate 出口"]
		if exit == nil || exit.Type() != want {
			t.Fatalf("%s: the exit is %v", mode, exit)
		}
		if got := members(t, admitted.cfg, "VPN Gate 出口"); !slices.Equal(got, []string{"VPN Gate JP vpn1", "VPN Gate JP vpn2"}) {
			t.Fatalf("%s: members = %v", mode, got)
		}
		if rules := ruleTexts(admitted); rules[0] != "DOMAIN-SUFFIX,ifconfig.me,VPN Gate 出口" {
			t.Fatalf("%s: rules = %v", mode, rules)
		}
		if warnings := admitted.report.GetWarnings(); len(warnings) != 0 {
			t.Fatalf("%s: warnings = %v", mode, warnings)
		}
	}

	// Without servers the rule rejects; the configuration still runs.
	admitted, err := admitProfile(Profile{Composition: composition(fmt.Sprintf(vpnGateRulePlan, "per-site"))}, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := admitted.cfg.Proxies["VPN Gate 出口"]; ok {
		t.Fatal("an exit without servers")
	}
	if rules := ruleTexts(admitted); rules[0] != "DOMAIN-SUFFIX,ifconfig.me,REJECT" {
		t.Fatalf("rules = %v", rules)
	}
}
