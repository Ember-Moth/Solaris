package engine

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"strings"
	"sync"
	"testing"
	"time"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

func proxiedGet(t *testing.T, mixedPort int, target string) string {
	t.Helper()
	proxy, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", mixedPort))
	transport := &http.Transport{Proxy: http.ProxyURL(proxy)}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	response, err := client.Get(target)
	if err != nil {
		t.Fatalf("GET %s through mixed port %d: %v", target, mixedPort, err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func listening(port int) bool {
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), time.Second)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

func TestOnlyOneEnginePerProcess(t *testing.T) {
	openEngine(t, TunNone)
	if _, err := New(Options{HomeDir: testHome, Tun: TunNone}); err == nil {
		t.Fatal("a second engine opened")
	}
}

// The core version comes from the binary's build info. Test binaries record their
// dependencies only from Go 1.27 on, which go.mod's toolchain line asks for; an older
// toolchain leaves mihomo's placeholder, 1.10.0.
func TestCoreVersionIsTheMihomoInGoMod(t *testing.T) {
	engine := openEngine(t, TunNone)
	want := requiredVersion(t, mihomoModule)
	if got := engine.CoreVersion(); got != want || C.Version != want {
		t.Fatalf("core version %q, mihomo's own %q; go.mod requires %s", got, C.Version, want)
	}
}

// requiredVersion reads the version of a required module from the module's go.mod.
func requiredVersion(t *testing.T, module string) string {
	t.Helper()
	source, err := os.ReadFile(filepath.Join("..", "..", "go.mod"))
	if err != nil {
		t.Fatal(err)
	}
	for line := range strings.Lines(string(source)) {
		fields := strings.Fields(line)
		if len(fields) > 0 && fields[0] == "require" {
			fields = fields[1:]
		}
		if len(fields) >= 2 && fields[0] == module {
			return fields[1]
		}
	}
	t.Fatalf("go.mod does not require %s", module)
	return ""
}

func TestStartProxiesTrafficAndStopClosesThePort(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	port := porttest.Free(t)
	if _, err := engine.Start(directProfile(port, server.URL+"/generate_204")); err != nil {
		t.Fatal(err)
	}
	if engine.State() != controlpb.RunState_RUN_STATE_RUNNING {
		t.Fatalf("state = %v", engine.State())
	}
	before := engine.Traffic()
	if body := proxiedGet(t, port, server.URL); body != "hello through mihomo" {
		t.Fatalf("body = %q", body)
	}
	after := engine.Traffic()
	if after.GetDownTotalBytes() <= before.GetDownTotalBytes() {
		t.Errorf("download total did not grow: %d -> %d", before.GetDownTotalBytes(), after.GetDownTotalBytes())
	}
	status := engine.Status()
	if status.GetMode() != controlpb.Mode_MODE_RULE || status.GetStartedAtUnixMs() == 0 || status.GetTunEnabled() {
		t.Errorf("status = %v", status)
	}

	if _, err := engine.Start(directProfile(port, server.URL)); !errors.Is(err, ErrAlreadyRunning) {
		t.Errorf("second Start = %v, want ErrAlreadyRunning", err)
	}
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	if listening(port) {
		t.Error("the mixed port is still open after Stop")
	}
	if err := engine.Stop(); err != nil {
		t.Errorf("stopping a stopped core = %v", err)
	}
	if _, err := engine.Reload(directProfile(port, server.URL)); !errors.Is(err, ErrNotRunning) {
		t.Errorf("Reload while stopped = %v, want ErrNotRunning", err)
	}
	if groups := engine.ListGroups(0, false); groups != nil {
		t.Errorf("groups while stopped = %v", groups)
	}
}

func TestReloadMovesThePort(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	first, second := porttest.Free(t), porttest.Free(t)
	if _, err := engine.Start(directProfile(first, server.URL)); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	if _, err := engine.Reload(directProfile(second, server.URL)); err != nil {
		t.Fatal(err)
	}
	if listening(first) || !listening(second) {
		t.Fatalf("after Reload: old port open=%v, new port open=%v", listening(first), listening(second))
	}
	if _, err := engine.Reload(profileOf("rules: ['MATCH,NOWHERE']\n")); !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("Reload with a broken profile = %v", err)
	}
	if !listening(second) || engine.State() != controlpb.RunState_RUN_STATE_RUNNING {
		t.Fatal("a rejected Reload disturbed the running core")
	}
}

func TestStartFailsWhenThePortIsTaken(t *testing.T) {
	engine := openEngine(t, TunNone)
	taken, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer taken.Close()
	port := taken.Addr().(*net.TCPAddr).Port
	_, err = engine.Start(directProfile(port, "http://127.0.0.1:1/"))
	if !errors.Is(err, ErrApplyFailed) || !isListenError(err) {
		t.Fatalf("Start on a taken port = %v, want ErrApplyFailed with the listen error", err)
	}
	status := engine.Status()
	if status.GetState() != controlpb.RunState_RUN_STATE_FAILED || status.GetLastError() == nil {
		t.Fatalf("status = %v", status)
	}
	if err := engine.Stop(); err != nil || engine.State() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("Stop after a failed start = %v, state %v", err, engine.State())
	}
}

// A Reload to a taken port fails with the reason and leaves the core on its old port;
// mihomo alone would close the old port first.
func TestReloadToATakenPortKeepsTheOldOne(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	port := porttest.Free(t)
	if _, err := engine.Start(directProfile(port, server.URL)); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	taken, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer taken.Close()
	_, err = engine.Reload(directProfile(taken.Addr().(*net.TCPAddr).Port, server.URL))
	if !errors.Is(err, ErrApplyFailed) || !isListenError(err) {
		t.Fatalf("Reload to a taken port = %v, want ErrApplyFailed with the listen error", err)
	}
	if got := proxiedGet(t, port, server.URL); got != "hello through mihomo" {
		t.Fatalf("through the old port: %q", got)
	}
	if status := engine.Status(); status.GetState() != controlpb.RunState_RUN_STATE_RUNNING || status.GetLastError() != nil {
		t.Fatalf("status = %v", status)
	}
}

// Rule types read as profiles write them; a type added upstream needs its keyword.
func TestEveryRuleTypeHasItsKeyword(t *testing.T) {
	for ruleType := C.RuleType(0); ruleType.String() != "Unknown"; ruleType++ {
		if ruleKeywords[ruleType] == "" {
			t.Errorf("%s has no keyword", ruleType)
		}
	}
	if got := ruleKeyword("DstPort"); got != "DST-PORT" {
		t.Errorf("DstPort reads as %q", got)
	}
}

// A proxy whose test failed reads 0; one never tested has no delay.
func TestFailedTestsTellFromUntested(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	source := fmt.Sprintf(`mixed-port: %d
proxies:
  - {name: dead, type: http, server: 127.0.0.1, port: %d}
proxy-groups:
  - {name: PROXY, type: select, url: %s, proxies: [DIRECT, dead]}
rules:
  - MATCH,PROXY
`, porttest.Free(t), porttest.Free(t), server.URL+"/generate_204")
	if _, err := engine.Start(Profile{Config: []byte(source)}); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	if err := engine.HealthCheck(context.Background(), "PROXY"); err != nil {
		t.Fatal(err)
	}
	delays := map[string]*uint32{}
	for _, group := range engine.ListGroups(controlpb.GroupSort_GROUP_SORT_DELAY, false) {
		for _, proxy := range group.GetProxies() {
			delays[group.GetName()+"/"+proxy.GetName()] = proxy.DelayMs
		}
	}
	if delay := delays["PROXY/DIRECT"]; delay == nil || *delay == 0 {
		t.Errorf("DIRECT after its test: %v", delay)
	}
	if delay := delays["PROXY/dead"]; delay == nil || *delay != 0 {
		t.Errorf("dead after its test: %v, want 0", delay)
	}
	if delay := delays[globalGroup+"/dead"]; delay != nil {
		t.Errorf("GLOBAL tests with its own URL, which nothing tested: %v", *delay)
	}
}

func isListenError(err error) bool {
	var opError *net.OpError
	return errors.As(err, &opError) && opError.Op == "listen"
}

func TestQueriesAndOperations(t *testing.T) {
	engine := openEngine(t, TunNone)
	server := target(t)
	port := porttest.Free(t)
	testURL := server.URL + "/generate_204"
	if _, err := engine.Start(directProfile(port, testURL)); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()

	groups := engine.ListGroups(controlpb.GroupSort_GROUP_SORT_UNSPECIFIED, false)
	if len(groups) != 2 || groups[0].GetName() != "PROXY" || groups[1].GetName() != globalGroup {
		t.Fatalf("groups = %v", groups)
	}
	proxyGroup := groups[0]
	if proxyGroup.GetNow() != "direct-a" || proxyGroup.GetTestUrl() != testURL || len(proxyGroup.GetProxies()) != 2 {
		t.Fatalf("PROXY = %v", proxyGroup)
	}
	for _, proxy := range proxyGroup.GetProxies() {
		if proxy.DelayMs != nil {
			t.Errorf("%s has a delay before any test: %d", proxy.GetName(), proxy.GetDelayMs())
		}
	}
	if err := engine.HealthCheck(context.Background(), "PROXY"); err != nil {
		t.Fatal(err)
	}
	group, err := engine.GetGroup("PROXY", controlpb.GroupSort_GROUP_SORT_DELAY)
	if err != nil {
		t.Fatal(err)
	}
	for _, proxy := range group.GetProxies() {
		if proxy.GetDelayMs() == 0 {
			t.Errorf("%s has no delay after a health check", proxy.GetName())
		}
	}
	if delay, err := engine.TestDelay(context.Background(), "direct-b", testURL, time.Second); err != nil || delay == 0 {
		t.Errorf("TestDelay = %d, %v", delay, err)
	}
	if _, err := engine.TestDelay(context.Background(), "nowhere", testURL, time.Second); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("TestDelay of an unknown proxy = %v", err)
	}

	if err := engine.SelectProxy("PROXY", "direct-b"); err != nil {
		t.Fatal(err)
	}
	if err := engine.SelectProxy("PROXY", "nowhere"); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("selecting an unknown proxy = %v", err)
	}
	if err := engine.SelectProxy("direct-a", "direct-b"); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("selecting in a proxy = %v", err)
	}
	if _, err := engine.GetGroup("nowhere", 0); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("GetGroup of an unknown group = %v", err)
	}
	if err := engine.SelectProxy("PROXY", "direct-a"); err != nil {
		t.Fatal(err)
	}

	if err := engine.SetMode(controlpb.Mode_MODE_GLOBAL); err != nil {
		t.Fatal(err)
	}
	if mode := engine.Status().GetMode(); mode != controlpb.Mode_MODE_GLOBAL {
		t.Errorf("mode = %v", mode)
	}
	if err := engine.SetMode(controlpb.Mode(42)); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("unknown mode = %v", err)
	}
	if err := engine.SetMode(controlpb.Mode_MODE_RULE); err != nil {
		t.Fatal(err)
	}

	rules := engine.ListRules()
	if len(rules) != 1 || rules[0].GetType() != "MATCH" || rules[0].GetProxy() != "PROXY" {
		t.Errorf("rules = %v", rules)
	}
	if providers := engine.ListProviders(); len(providers) != 0 {
		t.Errorf("providers = %v; the inline group providers are internal", providers)
	}
	if err := engine.UpdateProvider(controlpb.ProviderKind_PROVIDER_KIND_PROXY, "PROXY"); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("updating an internal provider = %v", err)
	}

	// Hold a connection open through the proxy and find it in the snapshot.
	proxyURL, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", port))
	conn, err := net.Dial("tcp", proxyURL.Host)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	host := strings.TrimPrefix(server.URL, "http://")
	fmt.Fprintf(conn, "CONNECT %s HTTP/1.1\r\nHost: %s\r\n\r\n", host, host)
	reply := make([]byte, 64)
	if _, err := conn.Read(reply); err != nil {
		t.Fatal(err)
	}
	var id string
	for deadline := time.Now().Add(5 * time.Second); id == "" && time.Now().Before(deadline); {
		for _, c := range engine.Connections().GetConnections() {
			if c.GetDestination() == host && c.GetRule() == "MATCH" {
				id = c.GetId()
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if id == "" {
		t.Fatal("the open connection is not in the snapshot")
	}
	if engine.Status().GetConnectionCount() == 0 {
		t.Error("status counts no connection")
	}
	if err := engine.CloseConnection(id); err != nil {
		t.Fatal(err)
	}
	if err := engine.CloseConnection(id); !errors.Is(err, ErrInvalidArgument) {
		t.Errorf("closing a closed connection = %v", err)
	}
	if err := engine.CloseAllConnections(); err != nil {
		t.Fatal(err)
	}
	if err := engine.FlushDNSCache(); err != nil {
		t.Fatal(err)
	}
	if err := engine.FlushFakeIP(); err != nil {
		t.Fatal(err)
	}
}

func TestWatchLogsSeesParseErrors(t *testing.T) {
	engine := openEngine(t, TunNone)
	var mu sync.Mutex
	var lines []string
	stop := engine.WatchLogs(func(entry *controlpb.LogEntry) {
		mu.Lock()
		lines = append(lines, entry.GetMessage())
		mu.Unlock()
	})
	_, _ = engine.Validate(directProfile(porttest.Free(t), "http://127.0.0.1:1/"))
	deadline := time.Now().Add(5 * time.Second)
	for {
		mu.Lock()
		joined := strings.Join(lines, "\n")
		mu.Unlock()
		if strings.Contains(joined, "Initial configuration complete") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no parse log line; got:\n%s", joined)
		}
		time.Sleep(20 * time.Millisecond)
	}
	stop()
}

// After 50 start/stop cycles the goroutine and file descriptor counts are back where they
// started.
func TestStartStopDoesNotLeak(t *testing.T) {
	if testing.Short() {
		t.Skip("50 start/stop cycles")
	}
	engine := openEngine(t, TunNone)
	server := target(t)
	port := porttest.Free(t)
	profile := directProfile(port, server.URL+"/generate_204")
	cycle := func() {
		if _, err := engine.Start(profile); err != nil {
			t.Fatal(err)
		}
		proxiedGet(t, port, server.URL)
		if err := engine.Stop(); err != nil {
			t.Fatal(err)
		}
	}
	// mihomo starts some process-wide workers and opens cache.db on first use.
	cycle()
	cycle()
	goroutines, fds := settle(0, 0)
	for range 50 {
		cycle()
	}
	afterGoroutines, afterFDs := settle(goroutines, fds)
	if afterGoroutines > goroutines || (fds >= 0 && afterFDs > fds) {
		var dump strings.Builder
		_ = pprof.Lookup("goroutine").WriteTo(&dump, 1)
		t.Fatalf("after 50 cycles: goroutines %d -> %d, descriptors %d -> %d\n%s",
			goroutines, afterGoroutines, fds, afterFDs, dump.String())
	}
	t.Logf("goroutines %d -> %d, descriptors %d -> %d", goroutines, afterGoroutines, fds, afterFDs)
}

// settle waits for background work to wind down and returns the goroutine and
// descriptor counts, stopping early once both are at or below the targets.
func settle(goroutines, fds int) (int, int) {
	var g, f int
	for deadline := time.Now().Add(15 * time.Second); ; {
		runtime.GC()
		time.Sleep(100 * time.Millisecond)
		g, f = runtime.NumGoroutine(), openDescriptors()
		if (goroutines > 0 && g <= goroutines && f <= fds) || time.Now().After(deadline) {
			return g, f
		}
		if goroutines == 0 {
			// Measuring the baseline: wait until two readings agree.
			time.Sleep(500 * time.Millisecond)
			if runtime.NumGoroutine() == g && openDescriptors() == f {
				return g, f
			}
		}
	}
}

// openDescriptors counts this process's open files, or returns -1 where it cannot.
func openDescriptors() int {
	for _, dir := range []string{"/proc/self/fd", "/dev/fd"} {
		if entries, err := os.ReadDir(dir); err == nil {
			return len(entries)
		}
	}
	return -1
}
