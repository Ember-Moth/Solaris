//go:build linux && tuntest

package engine

// The fd-mode test plays the macOS extension on Linux: it creates a TUN, gives it the
// network FDNetwork names, routes the target into it and hands the descriptor to an
// engine in fd mode. Like the owned-TUN test it compiles only with -tags tuntest, runs
// only with SOLARIS_TUN_TEST=1 and refuses the host's namespace (tun_linux_test.go).

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/listener"
	"golang.org/x/sys/unix"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const fdDevice = "mdfd0"

func TestFDTUN(t *testing.T) {
	if os.Getenv("SOLARIS_TUN_TEST") != "1" {
		t.Skip("set SOLARIS_TUN_TEST=1 and run inside a network namespace")
	}
	requireIsolatedNamespace(t)
	target := os.Getenv("SOLARIS_TUN_TARGET")
	outbound := os.Getenv("SOLARIS_TUN_OUTBOUND")
	if target == "" || outbound == "" {
		t.Fatal("SOLARIS_TUN_TARGET names the HTTP server outside the namespace, SOLARIS_TUN_OUTBOUND the link to it")
	}
	stacks := strings.Split(os.Getenv("SOLARIS_TUN_STACKS"), ",")
	if stacks[0] == "" {
		stacks = []string{"system"}
	}
	for _, stack := range stacks {
		t.Run(stack, func(t *testing.T) { fdTUN(t, stack, target, outbound) })
		if breaksAllNetworks(stack) {
			t.Run(stack+" including all networks", func(t *testing.T) { fdTUNAllNetworks(t, stack, target, outbound) })
		}
	}
}

func fdTUN(t *testing.T, stack, target, outbound string) {
	address, err := url.Parse(target)
	if err != nil {
		t.Fatal(err)
	}
	host := hostTUNDevice(t, address.Hostname())

	engine, err := New(Options{HomeDir: testHome, Tun: TunFD, TunFD: host})
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()
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

	// mihomo's own dials leave through the link to the server, as the extension's bypass
	// its tunnel; the hosts entry answers the query that the TUN's DNS address receives.
	source := func(hijack string) Profile {
		return Profile{Config: []byte(fmt.Sprintf(`mode: rule
log-level: debug
interface-name: %s
proxies: []
rules: ['MATCH,DIRECT']
hosts: {through-tun.test: %s}
dns: {nameserver: ['udp://127.0.0.1:1'], enhanced-mode: redir-host, use-hosts: true}
tun: {stack: %s, dns-hijack: [%s]}
`, outbound, address.Hostname(), stack, hijack))}
	}
	profile := source("'any:53'")

	if _, err := engine.Start(profile); err != nil {
		t.Fatalf("Start in fd mode: %v", err)
	}
	if !engine.Status().GetTunEnabled() {
		t.Fatal("status reports the TUN off")
	}
	requireTUNDescriptors(t, 2) // the host's and mihomo's duplicate
	throughTUN(t, engine, target)
	if addresses := lookupThroughTUN(t, "through-tun.test"); len(addresses) != 1 || addresses[0] != address.Hostname() {
		t.Fatalf("the TUN's DNS address answered %v", addresses)
	}

	// Unchanged TUN settings keep mihomo's TUN and its duplicate.
	running := listener.LastTunConf.FileDescriptor
	if _, err := engine.Reload(profile); err != nil {
		t.Fatal(err)
	}
	if again := listener.LastTunConf.FileDescriptor; again != running {
		t.Fatalf("Reload replaced the duplicate: %d -> %d", running, again)
	}
	// Changed ones get a new duplicate; mihomo closes the old one.
	if _, err := engine.Reload(source("'0.0.0.0:53'")); err != nil {
		t.Fatal(err)
	}
	if again := listener.LastTunConf.FileDescriptor; again == running {
		t.Fatal("Reload kept the duplicate although the TUN changed")
	}
	requireTUNDescriptors(t, 2)
	throughTUN(t, engine, target)

	// Stop leaves the host's descriptor, and so its TUN, in place for the next Start.
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	requireTUNDescriptors(t, 1)
	if _, err := net.InterfaceByName(fdDevice); err != nil {
		t.Fatalf("the TUN went away with Stop: %v", err)
	}
	if _, err := engine.Start(profile); err != nil {
		t.Fatalf("Start again: %v", err)
	}
	requireTUNDescriptors(t, 2)
	throughTUN(t, engine, target)
	if err := engine.Close(); err != nil {
		t.Fatal(err)
	}
	requireTUNDescriptors(t, 1)
}

// fdTUNAllNetworks runs a stack that NetworkExtension breaks once the tunnel includes all
// networks (D19): the engine runs the default fd stack in its place, and traffic still flows.
func fdTUNAllNetworks(t *testing.T, stack, target, outbound string) {
	address, err := url.Parse(target)
	if err != nil {
		t.Fatal(err)
	}
	host := hostTUNDevice(t, address.Hostname())
	engine, err := New(Options{HomeDir: testHome, Tun: TunFD, TunFD: host, IncludeAllNetworks: true})
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()
	profile := Profile{Config: []byte(fmt.Sprintf(`mode: rule
interface-name: %s
proxies: []
rules: ['MATCH,DIRECT']
dns: {nameserver: ['udp://127.0.0.1:1'], enhanced-mode: redir-host}
`, outbound)), Options: RunOptions{Stack: stack}}
	if _, err := engine.Start(profile); err != nil {
		t.Fatalf("Start in fd mode: %v", err)
	}
	if got := strings.ToLower(listener.LastTunConf.Stack.String()); got != defaultFDStack() {
		t.Fatalf("mihomo runs the %s stack, want %s", got, defaultFDStack())
	}
	throughTUN(t, engine, target)
}

// hostTUNDevice does what the extension and NetworkExtension do: a TUN with FDNetwork's
// addresses and a route that sends the target into it. The descriptor is closed, and the
// TUN removed, when the test ends.
func hostTUNDevice(t *testing.T, targetHost string) int {
	t.Helper()
	fd, err := unix.Open("/dev/net/tun", unix.O_RDWR|unix.O_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = unix.Close(fd)
		waitForDevice(t, fdDevice, false)
	})
	request, err := unix.NewIfreq(fdDevice)
	if err != nil {
		t.Fatal(err)
	}
	request.SetUint16(unix.IFF_TUN | unix.IFF_NO_PI)
	if err := unix.IoctlIfreq(fd, unix.TUNSETIFF, request); err != nil {
		t.Fatal(err)
	}
	for _, args := range [][]string{
		{"addr", "add", FDNetwork.Inet4.String(), "dev", fdDevice},
		{"link", "set", fdDevice, "mtu", fmt.Sprint(FDNetwork.MTU), "up"},
		{"route", "add", targetHost + "/32", "dev", fdDevice},
	} {
		if output, err := exec.Command("ip", args...).CombinedOutput(); err != nil {
			t.Fatalf("ip %s: %v\n%s", strings.Join(args, " "), err, output)
		}
	}
	return fd
}

func waitForDevice(t *testing.T, name string, present bool) {
	t.Helper()
	for deadline := time.Now().Add(10 * time.Second); ; {
		_, err := net.InterfaceByName(name)
		if (err == nil) == present {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s present = %v, want %v", name, err == nil, present)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// requireTUNDescriptors counts this process's descriptors on /dev/net/tun.
func requireTUNDescriptors(t *testing.T, want int) {
	t.Helper()
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, entry := range entries {
		if link, err := os.Readlink(filepath.Join("/proc/self/fd", entry.Name())); err == nil && link == "/dev/net/tun" {
			count++
		}
	}
	if count != want {
		t.Fatalf("%d descriptors on /dev/net/tun, want %d", count, want)
	}
}

// throughTUN fetches the target, which the route sends into the TUN, and checks that
// mihomo carried the traffic.
func throughTUN(t *testing.T, engine *Engine, target string) {
	t.Helper()
	before := engine.Traffic()
	if body := plainGet(t, target); !strings.Contains(body, "through-tun") {
		t.Fatalf("body = %q", body)
	}
	after := engine.Traffic()
	if after.GetDownTotalBytes() <= before.GetDownTotalBytes() || after.GetUpTotalBytes() <= before.GetUpTotalBytes() {
		t.Fatalf("mihomo carried no traffic: %v -> %v", before, after)
	}
}

// lookupThroughTUN asks the address the extension makes the system's resolver.
func lookupThroughTUN(t *testing.T, name string) []string {
	t.Helper()
	server := net.JoinHostPort(FDNetwork.DNSServer().String(), "53")
	resolver := &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		var dialer net.Dialer
		return dialer.DialContext(ctx, "udp", server)
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	addresses, err := resolver.LookupHost(ctx, name)
	if err != nil {
		t.Fatalf("look up %s at %s: %v", name, server, err)
	}
	return addresses
}
