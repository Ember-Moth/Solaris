package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

const tcpTest = controlpb.DelayTest_DELAY_TEST_TCP

// tcpServer takes TCP connections on loopback and closes them.
func tcpServer(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			_ = conn.Close()
		}
	}()
	return listener.Addr().(*net.TCPAddr).Port
}

// tcpProfile has a node that takes connections, one that refuses them, and the entries a
// TCP test leaves out: a UDP node, a chained node, a load-balance group and DIRECT. Its
// groups that test on their own do so against testURL.
func tcpProfile(t *testing.T, testURL string) admission.Profile {
	t.Helper()
	source := fmt.Sprintf(`mixed-port: %d
proxies:
  - {name: live, type: http, server: 127.0.0.1, port: %d}
  - {name: dead, type: http, server: 127.0.0.1, port: %d}
  - {name: quic, type: hysteria2, server: 127.0.0.1, port: %d, password: secret}
  - {name: chained, type: http, server: 127.0.0.1, port: %d, dialer-proxy: live}
  - {name: direct-a, type: direct}
  - {name: direct-b, type: direct}
proxy-groups:
  - {name: PROXY, type: select, proxies: [inner, live, dead, quic, chained, spread, DIRECT]}
  - {name: inner, type: select, proxies: [live]}
  - {name: spread, type: load-balance, url: %[6]s, proxies: [live, dead]}
  - {name: backup, type: fallback, url: %[6]s, proxies: [direct-a, direct-b]}
rules:
  - MATCH,PROXY
`, porttest.Free(t), tcpServer(t), porttest.Free(t), porttest.Free(t), tcpServer(t), testURL)
	return admission.Profile{Config: []byte(source)}
}

// startTCPProfile starts tcpProfile and waits for the first URL test of its groups that
// test on their own: mihomo reads a delay without its lock, so reading one while that
// test records it is a race (docs/maintenance.md §3).
func startTCPProfile(t *testing.T, engine *Engine) (profile admission.Profile, testURL string) {
	t.Helper()
	testURL = target(t).URL + "/generate_204"
	profile = tcpProfile(t, testURL)
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	urlTested(t, testURL)
	return profile, testURL
}

// urlTested waits until the members of spread and backup have a URL test of testURL.
func urlTested(t *testing.T, testURL string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for _, name := range []string{"live", "dead", "direct-a", "direct-b"} {
		proxy, ok := findProxy(name)
		for ok && len(proxy.ExtraDelayHistories()[testURL].History) == 0 {
			if time.Now().After(deadline) {
				t.Fatalf("%s has no URL test of %s", name, testURL)
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
}

// tcpDelays reads each member's TCP delay as "group/member"; nil when untested.
func tcpDelays(engine *Engine) map[string]*uint32 {
	delays := map[string]*uint32{}
	for _, group := range engine.ListGroups(controlpb.GroupSort_GROUP_SORT_UNSPECIFIED, false) {
		for _, proxy := range group.GetProxies() {
			delays[group.GetName()+"/"+proxy.GetName()] = proxy.TcpDelayMs
		}
	}
	return delays
}

func TestTCPHealthCheckMeasuresNodesWithATCPServer(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	profile, testURL := startTCPProfile(t, engine)
	for name, delay := range tcpDelays(engine) {
		if delay != nil {
			t.Errorf("%s has a TCP delay before any test: %d", name, *delay)
		}
	}
	if err := engine.HealthCheck(context.Background(), "PROXY", tcpTest); err != nil {
		t.Fatal(err)
	}
	delays := tcpDelays(engine)
	live := delays["PROXY/live"]
	if live == nil || *live == 0 {
		t.Fatalf("live after its test: %v", live)
	}
	if dead := delays["PROXY/dead"]; dead == nil || *dead != 0 {
		t.Errorf("dead after its test: %v, want 0", dead)
	}
	// A group reads as the node it uses now.
	if inner := delays["PROXY/inner"]; inner == nil || *inner != *live {
		t.Errorf("inner = %v, want live's %d", inner, *live)
	}
	for _, name := range []string{"quic", "chained", "spread", "DIRECT"} {
		if delay := delays["PROXY/"+name]; delay != nil {
			t.Errorf("%s has a TCP delay: %d", name, *delay)
		}
	}
	// Results belong to the node, whichever group lists it.
	if delay := delays["inner/live"]; delay == nil || *delay != *live {
		t.Errorf("inner/live = %v, want %d", delay, *live)
	}
	// The TCP test leaves the URL tests' history alone.
	group, err := engine.GetGroup("PROXY", 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, proxy := range group.GetProxies() {
		if proxy.DelayMs != nil {
			t.Errorf("%s has an HTTP delay after a TCP test: %d", proxy.GetName(), *proxy.DelayMs)
		}
	}

	if _, err := engine.Reload(profile); err != nil {
		t.Fatal(err)
	}
	urlTested(t, testURL)
	if delay := tcpDelays(engine)["PROXY/live"]; delay != nil {
		t.Errorf("live keeps its TCP delay over a reload: %d", *delay)
	}
}

// Groups that choose by their URL tests stay as the user pinned them; mihomo shows the
// pin as the group's "fixed" member.
func TestTCPHealthCheckLeavesPinnedGroups(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	startTCPProfile(t, engine)
	if err := engine.SelectProxy("backup", "direct-b"); err != nil {
		t.Fatal(err)
	}
	if err := engine.HealthCheck(context.Background(), "", tcpTest); err != nil {
		t.Fatal(err)
	}
	_, group, err := findGroup("backup")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := group.MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	var fields struct {
		Fixed string `json:"fixed"`
	}
	if err := json.Unmarshal(raw, &fields); err != nil || fields.Fixed != "direct-b" {
		t.Errorf("backup is pinned to %q (%v), want direct-b", fields.Fixed, err)
	}
}

func TestTCPTestDelay(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	startTCPProfile(t, engine)
	ctx := context.Background()
	for _, name := range []string{"live", "inner"} {
		if delay, err := engine.TestDelay(ctx, name, "", time.Second, tcpTest); err != nil || delay == 0 {
			t.Errorf("TestDelay(%s) = %d, %v", name, delay, err)
		}
	}
	if delay := tcpDelays(engine)["PROXY/live"]; delay == nil || *delay == 0 {
		t.Errorf("live after TestDelay: %v", delay)
	}
	if _, err := engine.TestDelay(ctx, "dead", "", time.Second, tcpTest); err == nil || errors.Is(err, ErrInvalidArgument) {
		t.Errorf("TestDelay(dead) = %v, want a failed test", err)
	}
	if delay := tcpDelays(engine)["PROXY/dead"]; delay == nil || *delay != 0 {
		t.Errorf("dead after TestDelay: %v, want 0", delay)
	}
	for _, name := range []string{"quic", "chained", "spread", "DIRECT"} {
		if _, err := engine.TestDelay(ctx, name, "", time.Second, tcpTest); !errors.Is(err, ErrInvalidArgument) {
			t.Errorf("TestDelay(%s) = %v, want ErrInvalidArgument", name, err)
		}
	}
	unknown := controlpb.DelayTest(7)
	if _, err := engine.TestDelay(ctx, "live", "", time.Second, unknown); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("TestDelay with an unknown test = %v", err)
	}
	if err := engine.HealthCheck(ctx, "PROXY", unknown); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("HealthCheck with an unknown test = %v", err)
	}
}
