//go:build darwin || linux

package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

var testHome string

func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "md-service-")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	testHome = home
	code := m.Run()
	_ = os.RemoveAll(home)
	os.Exit(code)
}

func TestDevelopmentServerRefusesRoot(t *testing.T) {
	if err := refuseRoot(0); !errors.Is(err, errRoot) {
		t.Fatalf("refuseRoot(0) = %v, want errRoot", err)
	}
	if err := refuseRoot(501); err != nil {
		t.Fatalf("refuseRoot(501) = %v, want nil", err)
	}
}

func developmentServer(t *testing.T) (*engine.Engine, *control.Dispatcher) {
	t.Helper()
	core, err := newDevelopmentEngine(testHome)
	if err != nil {
		t.Fatal(err)
	}
	dispatcher := newDevelopmentDispatcher(core)
	t.Cleanup(func() {
		dispatcher.Close()
		core.Close()
	})
	return core, dispatcher
}

func handle(t *testing.T, dispatcher *control.Dispatcher, request *controlpb.Request) *controlpb.Response {
	t.Helper()
	request.ProtocolVersion = product.ProtocolVersion
	request.Id = 1
	payload, err := proto.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	var response controlpb.Response
	if err := proto.Unmarshal(dispatcher.Handle(context.Background(), peer.Identity{UserID: "501"}, payload), &response); err != nil {
		t.Fatal(err)
	}
	return &response
}

func start(configYAML string, options *controlpb.RunOptions) *controlpb.Request {
	return &controlpb.Request{Method: &controlpb.Request_Start{Start: &controlpb.StartRequest{
		Source:  &controlpb.StartRequest_ConfigYaml{ConfigYaml: configYAML},
		Options: options,
	}}}
}

// A profile that asks for everything the development server must refuse.
func hostileProfile(mixedPort int) string {
	return fmt.Sprintf(`mixed-port: %d
allow-lan: true
bind-address: '*'
redir-port: 0
tun: {enable: true, stack: system, auto-route: true, auto-redirect: true, strict-route: true}
external-controller: 0.0.0.0:9090
proxies: []
rules: ['MATCH,DIRECT']
`, mixedPort)
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

func TestDevelopmentServerRunsWithoutTUN(t *testing.T) {
	core, _ := developmentServer(t)
	if core.TunMode() != admission.TunNone {
		t.Fatalf("the development engine runs in %v mode", core.TunMode())
	}
}

func TestDevelopmentServerRejectsTUNAndSystemProxy(t *testing.T) {
	core, dispatcher := developmentServer(t)
	profile := hostileProfile(freePort(t))
	for name, request := range map[string]*controlpb.Request{
		"start with TUN":          start(profile, &controlpb.RunOptions{TunEnabled: true}),
		"start with system proxy": start(profile, &controlpb.RunOptions{SystemProxyEnabled: true}),
		"set system proxy": {Method: &controlpb.Request_SetSystemProxy{
			SetSystemProxy: &controlpb.SetSystemProxyRequest{Enabled: true},
		}},
	} {
		if code := handle(t, dispatcher, request).GetError().GetCode(); code != control.CodeUnsupportedMethod {
			t.Errorf("%s: code = %q, want %q", name, code, control.CodeUnsupportedMethod)
		}
	}
	if core.State() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("state = %v after rejected starts", core.State())
	}
}

// The profile turns on the TUN and LAN access; the development server starts it with
// neither, on 127.0.0.1 only.
func TestDevelopmentServerKeepsTheProfileOnLoopback(t *testing.T) {
	_, dispatcher := developmentServer(t)
	port := freePort(t)
	if response := handle(t, dispatcher, start(hostileProfile(port), nil)); response.GetError() != nil {
		t.Fatalf("start = %v", response.GetError())
	}
	status := handle(t, dispatcher, &controlpb.Request{Method: &controlpb.Request_GetStatus{
		GetStatus: &controlpb.GetStatusRequest{},
	}}).GetGetStatus()
	if status.GetState() != controlpb.RunState_RUN_STATE_RUNNING || status.GetTunEnabled() || status.GetSystemProxyEnabled() {
		t.Fatalf("status = %v", status)
	}
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), time.Second)
	if err != nil {
		t.Fatalf("the mixed port is not on loopback: %v", err)
	}
	conn.Close()
	for _, address := range nonLoopbackAddresses(t) {
		if conn, err := net.DialTimeout("tcp", net.JoinHostPort(address, fmt.Sprint(port)), time.Second); err == nil {
			conn.Close()
			t.Errorf("the mixed port answers on %s", address)
		}
	}
	if conn, err := net.DialTimeout("tcp", "127.0.0.1:9090", 200*time.Millisecond); err == nil {
		conn.Close()
		t.Error("something listens on the profile's external-controller port")
	}
}

func nonLoopbackAddresses(t *testing.T) []string {
	t.Helper()
	addresses, err := net.InterfaceAddrs()
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, address := range addresses {
		if prefix, ok := address.(*net.IPNet); ok && !prefix.IP.IsLoopback() && prefix.IP.To4() != nil {
			out = append(out, prefix.IP.String())
		}
	}
	return out
}
