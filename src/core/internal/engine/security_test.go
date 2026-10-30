package engine

import (
	"context"
	"errors"
	"fmt"
	"go/ast"
	"go/build"
	"go/parser"
	"go/token"
	"go/types"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/metacubex/mihomo/adapter/provider"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	"github.com/metacubex/mihomo/hub/route"
)

// Regression tests for the requirements of docs/architecture.md §3.3, in order.

// #1: no build carries the cmfa tag, which turns IsSafePath into "always true".
func TestSecurity1NoCMFABuild(t *testing.T) {
	if features.CMFA {
		t.Fatal("this binary was built with the cmfa tag")
	}
	// cmfa.go joins the package only under the tag, and does not type-check.
	tagged := build.Default
	tagged.BuildTags = []string{"cmfa"}
	if ok, err := tagged.MatchFile(".", "cmfa.go"); err != nil || !ok {
		t.Fatalf("cmfa.go is not part of a cmfa build: %v", err)
	}
	if ok, _ := build.Default.MatchFile(".", "cmfa.go"); ok {
		t.Fatal("cmfa.go is part of the normal build")
	}
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "cmfa.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := (&types.Config{}).Check("engine", fset, []*ast.File{file}, nil); err == nil {
		t.Fatal("cmfa.go type-checks, so a cmfa build would succeed")
	}
	if err := checkPathSandbox(C.Path.HomeDir()); err != nil {
		t.Fatalf("the sandbox self-check fails in a normal build: %v", err)
	}
}

// #2: the controller fields are cleared last, after the overrides, in every mode.
func TestSecurity2ControllerIsAlwaysCleared(t *testing.T) {
	const controller = `
external-controller: 0.0.0.0:9090
external-controller-tls: 0.0.0.0:9443
external-controller-unix: mihomo.sock
external-controller-pipe: \\.\pipe\mihomo
external-doh-server: /dns-query
external-ui: ui
external-ui-name: zashboard
external-ui-url: https://example.invalid/ui.zip
secret: hunter2
`
	overrides := [][]byte{
		[]byte("external-controller: 0.0.0.0:9091\nexternal-controller-pipe: '\\\\.\\pipe\\override'\n"),
		[]byte("secret: again\nexternal-doh-server: /again\nexternal-ui: ui\n"),
	}
	for _, mode := range []TunMode{TunNone, TunOwned} {
		cfg := admitted(t, mode, Profile{Config: []byte(minimal + controller), Overrides: overrides})
		c := cfg.Controller
		if c.ExternalController != "" || c.ExternalControllerTLS != "" || c.ExternalControllerUnix != "" ||
			c.ExternalControllerPipe != "" || c.ExternalDohServer != "" || c.Secret != "" ||
			c.ExternalUI != "" || c.ExternalUIName != "" || c.ExternalUIURL != "" {
			t.Fatalf("%v: controller survived admission: %+v", mode, c)
		}
	}

	// And through Start, which hands the admitted configuration to hub.ApplyConfig: that
	// starts whatever controller the configuration names (#3 starts one the same way).
	engine := openEngine(t, TunNone)
	dir, err := os.MkdirTemp("", "md-ctl-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	socket := filepath.Join(dir, "c.sock")
	api := freePort(t)
	profile := directProfile(freePort(t), "http://127.0.0.1:1/",
		fmt.Sprintf("external-controller: 127.0.0.1:%d\nexternal-controller-unix: '%s'", api, socket))
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()
	// Controllers start in the background, a requested one within milliseconds.
	for deadline := time.Now().Add(300 * time.Millisecond); time.Now().Before(deadline); {
		if listening(api) || unixListening(socket) {
			t.Fatal("the started core opened the controller its profile asked for")
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// #3: embed mode is on, so even a controller started by mistake cannot restart the
// process, rewrite the configuration or upgrade the core.
func TestSecurity3EmbedModeGuardsAStrayController(t *testing.T) {
	openEngine(t, TunNone)
	dir, err := os.MkdirTemp("", "md-ctl-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	socket := filepath.Join(dir, "c.sock")
	// The one controller these tests start, on purpose and through route, not the pipeline.
	// Closing it makes mihomo log "External controller unix serve error: http: Server closed".
	route.ReCreateServer(&route.Config{UnixAddr: socket})
	defer stopStrayController(t, socket)

	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", socket)
		},
	}}
	status := func(method, path string) int {
		request, err := http.NewRequest(method, "http://controller"+path, strings.NewReader("{}"))
		if err != nil {
			t.Fatal(err)
		}
		response, err := client.Do(request)
		if err != nil {
			t.Fatalf("%s %s: %v", method, path, err)
		}
		response.Body.Close()
		return response.StatusCode
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		conn, err := net.Dial("unix", socket)
		if err == nil {
			conn.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the stray controller never listened: %v", err)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if got := status(http.MethodGet, "/version"); got != http.StatusOK {
		t.Fatalf("GET /version = %d; the controller is not really up", got)
	}
	for _, call := range []struct{ method, path string }{
		{http.MethodPost, "/restart"},
		{http.MethodPut, "/configs"},
		{http.MethodPatch, "/configs"},
		{http.MethodPost, "/configs/geo"},
		{http.MethodPost, "/upgrade"},
		{http.MethodPost, "/upgrade/geo"},
		{http.MethodPatch, "/rules/disable"},
	} {
		if got := status(call.method, call.path); got < 400 {
			t.Errorf("%s %s = %d, want it refused", call.method, call.path, got)
		}
	}
}

// stopStrayController closes the controller and waits until the socket is gone:
// ReCreateServer works in the background, and no later test may find it still listening.
func stopStrayController(t *testing.T, socket string) {
	route.ReCreateServer(&route.Config{})
	for deadline := time.Now().Add(5 * time.Second); unixListening(socket); {
		if time.Now().After(deadline) {
			t.Error("the stray controller still listens")
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func unixListening(socket string) bool {
	conn, err := net.Dial("unix", socket)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

// #4: provider paths stay local by the platform's own rules, and IsSafePath stays on
// behind them: no SKIP_SAFE_PATH_CHECK, no SAFE_PATHS.
func TestSecurity4ProviderPathsStayInHome(t *testing.T) {
	escapes := []string{"../outside.yaml", "providers/../../outside.yaml", filepath.Join(os.TempDir(), "abs.yaml")}
	if runtime.GOOS == "windows" {
		escapes = append(escapes, `..\outside.yaml`, `providers\..\..\outside.yaml`, `C:\Windows\win.ini`,
			`\\server\share\p.yaml`, `C:relative.yaml`, `NUL`)
	}
	for _, kind := range []string{"proxy-providers", "rule-providers"} {
		for _, path := range escapes {
			source := minimal + kind + ":\n  p:\n    type: file\n    behavior: classical\n    path: '" + path + "'\n"
			if _, err := admit(profileOf(source), TunNone); !errors.Is(err, ErrInvalidConfig) {
				t.Errorf("%s path %q: admit = %v, want ErrInvalidConfig", kind, path, err)
			}
		}
	}
	local := minimal + "proxy-providers:\n  p:\n    type: http\n    url: http://127.0.0.1:1/p.yaml\n    path: providers/p.yaml\n"
	cfg := admitted(t, TunNone, profileOf(local))
	if cfg.Providers["p"] == nil {
		t.Fatal("a local provider path was refused")
	}

	for name, env := range map[string]string{
		"SKIP_SAFE_PATH_CHECK": "SKIP_SAFE_PATH_CHECK=true",
		"SAFE_PATHS":           "SAFE_PATHS=" + os.TempDir(),
	} {
		cmd := exec.Command(os.Args[0], "-test.run=^$")
		cmd.Env = append(os.Environ(), helperEnv+"=1", env)
		output, err := cmd.CombinedOutput()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 3 || !strings.Contains(string(output), "refused:") {
			t.Errorf("with %s the engine opened: %v\n%s", name, err, output)
		}
	}
}

// #5: node selections are stored, so a restart (and the next boot) returns to them.
func TestSecurity5SelectionSurvivesRestart(t *testing.T) {
	profile := profileOf(minimal + "profile: {store-selected: false}\n")
	profile.Overrides = [][]byte{[]byte("profile: {store-selected: false}\n")}
	if cfg := admitted(t, TunNone, profile); !cfg.Profile.StoreSelected {
		t.Fatal("store-selected is off")
	}

	engine := openEngine(t, TunNone)
	server := target(t)
	profile = directProfile(freePort(t), server.URL+"/generate_204", "profile: {store-selected: false}")
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	if err := engine.SelectProxy("PROXY", "direct-b"); err != nil {
		t.Fatal(err)
	}
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	group, err := engine.GetGroup("PROXY", 0)
	if err != nil {
		t.Fatal(err)
	}
	if group.GetNow() != "direct-b" {
		t.Fatalf("after a restart PROXY uses %q, want direct-b", group.GetNow())
	}
	// Leave the stored choice where the other tests expect it.
	if err := engine.SelectProxy("PROXY", "direct-a"); err != nil {
		t.Fatal(err)
	}
}

// #6 (docs/mesh.md §4): mesh outbounds only go out. Their state stays in the home
// directory, and EasyTier's options that serve other devices are off, wherever the
// outbound comes from: the profile, an override, a provider's payload, or what a
// provider loads and rewrites later.
func TestSecurity6MeshOutboundsOnlyGoOut(t *testing.T) {
	escapes := []string{"../outside", filepath.Join(os.TempDir(), "mesh-state")}
	for _, outbound := range []string{
		"{name: ts, type: tailscale, state-dir: '%s'}",
		"{name: zt, type: zerotier, network: 8056c2e21c000001, state-dir: '%s'}",
		"{name: et, type: easytier, network-name: n, network-secret: s, peers: ['tcp://198.51.100.1:11010'], state-dir: '%s'}",
	} {
		for _, path := range escapes {
			source := "mixed-port: 7890\nproxies:\n  - " + fmt.Sprintf(outbound, path) + "\nrules: ['MATCH,DIRECT']\n"
			_, err := admit(profileOf(source), TunNone)
			if !errors.Is(err, ErrInvalidConfig) {
				t.Errorf("%s: admit = %v, want ErrInvalidConfig", fmt.Sprintf(outbound, path), err)
				continue
			}
			// Without with_gvisor mihomo refuses every Tailscale outbound, so only the
			// other two show the path check here.
			if strings.Contains(outbound, "tailscale") && !features.WithGVisor {
				continue
			}
			if !strings.Contains(err.Error(), "not subpath of home") {
				t.Errorf("%s: refused for another reason: %v", fmt.Sprintf(outbound, path), err)
			}
		}
	}

	const serving = "{name: %s, type: easytier, network-name: n, network-secret: s, peers: ['tcp://198.51.100.1:11010'], " +
		"enable-exit-node: true, proxy-networks: [192.168.1.0/24], mapped-listeners: ['tcp://203.0.113.1:11010']}"
	source := "mixed-port: 7890\nrules: ['MATCH,DIRECT']\nproxies:\n  - " + fmt.Sprintf(serving, "et-profile") + `
proxy-providers:
  inline:
    type: inline
    payload:
      - ` + fmt.Sprintf(serving, "et-inline") + `
  remote:
    type: http
    url: http://127.0.0.1:1/p.yaml
    path: providers/remote.yaml
    override:
      override-expr: ['.type = "easytier"', '.enable-exit-node = true', '.proxy-networks = ["0.0.0.0/0"]']
`
	profile := profileOf(source)
	profile.Overrides = [][]byte{[]byte("prepend-proxies:\n  - " + fmt.Sprintf(serving, "et-override") + "\n")}
	admitted, err := admitProfile(profile, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	providers := admitted.doc["proxy-providers"].(map[string]any)
	outbounds := append(slices.Clone(admitted.doc["proxies"].([]any)),
		providers["inline"].(map[string]any)["payload"].([]any)...)
	if len(outbounds) != 3 {
		t.Fatalf("outbounds = %v", outbounds)
	}
	for _, raw := range outbounds {
		outbound := raw.(map[string]any)
		if outbound["enable-exit-node"] != false || outbound["proxy-networks"] != nil || outbound["mapped-listeners"] != nil {
			t.Errorf("%v still serves other devices", outbound)
		}
	}
	for _, name := range []string{"et-profile", "et-override"} {
		if _, ok := admitted.cfg.Proxies[name]; !ok {
			t.Errorf("%s did not build", name)
		}
	}

	// What the remote provider loads goes through its expressions, the guard last: an
	// outbound they make an EasyTier one, or one that came as one, ends up serving nobody.
	override := providers["remote"].(map[string]any)["override"].(map[string]any)
	if _, ok := providers["inline"].(map[string]any)["override"]; ok {
		t.Error("an inline provider without expressions got the guard")
	}
	for _, loaded := range []map[string]any{
		{"name": "ss", "type": "ss", "server": "a.example", "port": 1},
		{"name": "et", "type": "easytier", "mapped-listeners": []any{"tcp://203.0.113.1:1"}},
	} {
		for _, raw := range override["override-expr"].([]any) {
			expression, err := provider.NewOverrideExpr(raw.(string))
			if err != nil {
				t.Fatalf("%q: %v", raw, err)
			}
			if err := expression.Apply(loaded); err != nil {
				t.Fatalf("%q: %v", raw, err)
			}
		}
		if loaded["type"] != "easytier" || loaded["enable-exit-node"] != false ||
			len(loaded["proxy-networks"].([]any)) != 0 || len(loaded["mapped-listeners"].([]any)) != 0 {
			t.Errorf("loaded as %v", loaded)
		}
	}
}
