package engine

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/geodata/geodatatest"
)

// testHome is shared by every test: mihomo opens cache.db in the first home it uses and
// keeps it for the life of the process.
var testHome string

// helperEnv makes the test binary open one engine and report the result, for checks
// that need environment variables mihomo reads before main.
const helperEnv = "MIHOMO_DESKTOP_ENGINE_HELPER"

func TestMain(m *testing.M) {
	if os.Getenv(helperEnv) != "" {
		runHelper()
		return
	}
	dir, err := os.MkdirTemp("", "md-engine-")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	testHome = dir
	// Tests that only run the pipeline still resolve provider paths against the home.
	C.SetHomeDir(dir)
	// Composed configurations read GEO data; without it mihomo would download it.
	if err := geodatatest.Write(dir); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	code := m.Run()
	_ = os.RemoveAll(dir)
	os.Exit(code)
}

func runHelper() {
	home, err := os.MkdirTemp("", "md-engine-helper-")
	if err == nil {
		defer os.RemoveAll(home)
		var engine *Engine
		if engine, err = New(Options{HomeDir: home, Tun: TunNone}); err == nil {
			engine.Close()
		}
	}
	if err != nil {
		fmt.Println("refused:", err)
		os.Exit(3)
	}
	fmt.Println("opened")
}

func openEngine(t *testing.T, mode TunMode) *Engine {
	t.Helper()
	engine, err := New(Options{HomeDir: testHome, Tun: mode})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { engine.Close() })
	return engine
}

func freePort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	return listener.Addr().(*net.TCPAddr).Port
}

// target is a loopback HTTP server, so no test needs the internet.
func target(t *testing.T) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/generate_204" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		_, _ = w.Write([]byte("hello through mihomo"))
	}))
	t.Cleanup(server.Close)
	return server
}

// directProfile has two direct outbounds behind a selector, tested against testURL.
func directProfile(mixedPort int, testURL string, extra ...string) Profile {
	source := fmt.Sprintf(`mixed-port: %d
mode: rule
log-level: info
proxies:
  - {name: direct-a, type: direct}
  - {name: direct-b, type: direct}
proxy-groups:
  - name: PROXY
    type: select
    url: %s
    proxies: [direct-a, direct-b]
rules:
  - MATCH,PROXY
%s`, mixedPort, testURL, strings.Join(extra, "\n"))
	return Profile{Config: []byte(source)}
}
