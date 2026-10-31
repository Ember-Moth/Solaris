//go:build linux && tuntest

package engine

// The owned-TUN test creates a real TUN, so it only ever runs inside a network
// namespace (docs/architecture.md §11): it compiles only with -tags tuntest, runs only
// with SOLARIS_TUN_TEST=1, and refuses the host's namespace. CI builds the
// namespaces; see the tun job in .github/workflows/ci.yml.

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const tunDevice = "mdtun0"

func TestOwnedTUN(t *testing.T) {
	if os.Getenv("SOLARIS_TUN_TEST") != "1" {
		t.Skip("set SOLARIS_TUN_TEST=1 and run inside a network namespace")
	}
	requireIsolatedNamespace(t)
	target := os.Getenv("SOLARIS_TUN_TARGET")
	if target == "" {
		t.Fatal("SOLARIS_TUN_TARGET names the HTTP server outside the namespace")
	}
	stacks := strings.Split(os.Getenv("SOLARIS_TUN_STACKS"), ",")
	if stacks[0] == "" {
		stacks = []string{"system"}
	}
	for _, stack := range stacks {
		t.Run(stack, func(t *testing.T) { ownedTUN(t, stack, target) })
	}
}

func requireIsolatedNamespace(t *testing.T) {
	t.Helper()
	self, err := os.Readlink("/proc/self/ns/net")
	if err != nil {
		t.Fatal(err)
	}
	host, err := os.Readlink("/proc/1/ns/net")
	if err != nil {
		t.Fatalf("cannot compare with the host's network namespace: %v", err)
	}
	if self == host {
		t.Fatal("refusing to create a TUN in the host's network namespace")
	}
}

func ownedTUN(t *testing.T, stack, target string) {
	engine := openEngine(t, TunOwned)
	var mu sync.Mutex
	var lines []string
	stopLogs := engine.WatchLogs(func(entry *controlpb.LogEntry) {
		mu.Lock()
		lines = append(lines, entry.GetMessage())
		mu.Unlock()
	})
	defer stopLogs()
	defer func() {
		if t.Failed() {
			mu.Lock()
			t.Logf("mihomo log:\n%s", strings.Join(lines, "\n"))
			mu.Unlock()
		}
	}()

	baseline := networkState(t)
	// dns-mode disabled: otherwise sing-tun hands the TUN's DNS to systemd-resolved with
	// resolvectl, which the namespace does not isolate. resolvectl turns mdtun0 into an
	// index here, and resolved applies it to the host's interface with that index.
	profile := Profile{Config: []byte(fmt.Sprintf(`mode: rule
log-level: debug
proxies: []
rules: ['MATCH,DIRECT']
tun:
  stack: %s
  device: %s
  dns-mode: disabled
`, stack, tunDevice)), Options: RunOptions{TUN: true}}

	if _, err := engine.Start(profile); err != nil {
		t.Fatalf("Start with the TUN: %v", err)
	}
	defer engine.Stop()
	index := waitForInterface(t, true)
	if !engine.Status().GetTunEnabled() {
		t.Fatal("status reports the TUN off")
	}

	// The test's own request is not mihomo's, so the routes send it into the TUN;
	// mihomo's DIRECT dial is bound to the veth and leaves through it. The target must be
	// off the namespace's link: auto-route leaves on-link destinations to the main table.
	throughTUN(t, engine, target)

	// An unchanged TUN survives a reload.
	if _, err := engine.Reload(profile); err != nil {
		t.Fatal(err)
	}
	if again := waitForInterface(t, true); again != index {
		t.Fatalf("Reload rebuilt the TUN: index %d -> %d", index, again)
	}

	// Turning the TUN off removes it and its routes while the core keeps running.
	profile.Options.TUN = false
	if _, err := engine.Reload(profile); err != nil {
		t.Fatal(err)
	}
	waitForInterface(t, false)
	if state := networkState(t); state != baseline {
		t.Fatalf("routes after turning the TUN off:\n%s\nwant:\n%s", state, baseline)
	}

	profile.Options.TUN = true
	if _, err := engine.Reload(profile); err != nil {
		t.Fatal(err)
	}
	waitForInterface(t, true)
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	waitForInterface(t, false)
	if state := networkState(t); state != baseline {
		t.Fatalf("routes after Stop:\n%s\nwant:\n%s", state, baseline)
	}
}

// throughTUN fetches the target, which the routes send into the TUN, and checks that
// mihomo carried the traffic. mihomo counts the bytes it relays once it has passed them
// on, and with gVisor the response can reach the client before its bytes are counted, so
// the counts get a moment to catch up. A request that went around the TUN never shows up
// in them.
func throughTUN(t *testing.T, engine *Engine, target string) {
	t.Helper()
	before := engine.Traffic()
	if body := plainGet(t, target); !strings.Contains(body, "through-tun") {
		t.Fatalf("body = %q", body)
	}
	for deadline := time.Now().Add(5 * time.Second); ; {
		after := engine.Traffic()
		if after.GetDownTotalBytes() > before.GetDownTotalBytes() && after.GetUpTotalBytes() > before.GetUpTotalBytes() {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("mihomo carried no traffic: %v -> %v", before, after)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func plainGet(t *testing.T, target string) string {
	t.Helper()
	client := &http.Client{Timeout: 15 * time.Second, Transport: &http.Transport{Proxy: nil}}
	response, err := client.Get(target)
	if err != nil {
		t.Fatalf("GET %s: %v", target, err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func waitForInterface(t *testing.T, present bool) int {
	t.Helper()
	for deadline := time.Now().Add(10 * time.Second); ; {
		iface, err := net.InterfaceByName(tunDevice)
		if present && err == nil {
			return iface.Index
		}
		if !present && err != nil {
			return 0
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s present = %v, want %v", tunDevice, err == nil, present)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// networkState is everything the TUN may touch: links, policy rules and every table.
func networkState(t *testing.T) string {
	t.Helper()
	var state strings.Builder
	for _, args := range [][]string{
		{"-o", "link", "show"},
		{"-4", "rule", "show"},
		{"-6", "rule", "show"},
		{"-4", "route", "show", "table", "all"},
		{"-6", "route", "show", "table", "all"},
	} {
		output, err := exec.Command("ip", args...).CombinedOutput()
		if err != nil {
			t.Fatalf("ip %s: %v\n%s", strings.Join(args, " "), err, output)
		}
		fmt.Fprintf(&state, "# ip %s\n%s", strings.Join(args, " "), output)
	}
	return state.String()
}
