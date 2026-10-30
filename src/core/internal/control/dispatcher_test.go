package control

import (
	"context"
	"fmt"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata/geodatatest"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

// core is the one mihomo engine this test process may open, in development mode.
var core *engine.Engine

func TestMain(m *testing.M) {
	home, err := os.MkdirTemp("", "md-control-")
	if err == nil {
		// Composed configurations read GEO data; without it mihomo would download it.
		err = geodatatest.Write(home)
	}
	if err == nil {
		core, err = engine.New(engine.Options{HomeDir: home, Tun: admission.TunNone})
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	code := m.Run()
	core.Close()
	_ = os.RemoveAll(home)
	os.Exit(code)
}

// unlinked answers GetInfo like a service whose core has never started; every other
// method panics, which keeps the shared protocol vectors independent of mihomo.
type unlinked struct{ Engine }

func (unlinked) CoreVersion() string { return "unlinked" }

func (unlinked) State() controlpb.RunState { return controlpb.RunState_RUN_STATE_STOPPED }

func call(t *testing.T, dispatcher *Dispatcher, request *controlpb.Request) *controlpb.Response {
	t.Helper()
	return callAs(t, dispatcher, context.Background(), peer.Identity{}, request)
}

func callAs(t *testing.T, dispatcher *Dispatcher, ctx context.Context, caller peer.Identity, request *controlpb.Request) *controlpb.Response {
	t.Helper()
	payload, err := proto.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return decode(t, dispatcher.Handle(ctx, caller, payload))
}

func decode(t *testing.T, payload []byte) *controlpb.Response {
	t.Helper()
	var response controlpb.Response
	if err := proto.Unmarshal(payload, &response); err != nil {
		t.Fatal(err)
	}
	if response.GetProtocolVersion() != product.ProtocolVersion {
		t.Fatalf("response protocol version = %d", response.GetProtocolVersion())
	}
	return &response
}

func request(id uint64, method func(*controlpb.Request)) *controlpb.Request {
	value := &controlpb.Request{ProtocolVersion: product.ProtocolVersion, Id: id}
	method(value)
	return value
}

func getInfo(r *controlpb.Request) {
	r.Method = &controlpb.Request_GetInfo{GetInfo: &controlpb.GetInfoRequest{}}
}

func expectError(t *testing.T, response *controlpb.Response, id uint64, code string) {
	t.Helper()
	if response.GetId() != id {
		t.Fatalf("response id = %d, want %d", response.GetId(), id)
	}
	if got := response.GetError().GetCode(); got != code {
		t.Fatalf("error = %v, want code %q", response.GetError(), code)
	}
}

func expectOK(t *testing.T, response *controlpb.Response) {
	t.Helper()
	if response.GetError() != nil {
		t.Fatalf("error = %v", response.GetError())
	}
}

func startRequest(configYAML string, options *controlpb.RunOptions) func(*controlpb.Request) {
	return func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Start{Start: &controlpb.StartRequest{Source: &controlpb.StartRequest_ConfigYaml{ConfigYaml: configYAML}, Options: options}}
	}
}

func reloadRequest(configYAML string, options *controlpb.RunOptions) func(*controlpb.Request) {
	return func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Reload{Reload: &controlpb.ReloadRequest{Source: &controlpb.ReloadRequest_ConfigYaml{ConfigYaml: configYAML}, Options: options}}
	}
}

func stopRequest(r *controlpb.Request) {
	r.Method = &controlpb.Request_Stop{Stop: &controlpb.StopRequest{}}
}

func systemProxyRequest(enabled bool) func(*controlpb.Request) {
	return func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SetSystemProxy{SetSystemProxy: &controlpb.SetSystemProxyRequest{Enabled: enabled}}
	}
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

func profileYAML(mixedPort int) string {
	return fmt.Sprintf(`mixed-port: %d
proxies:
  - {name: direct-a, type: direct}
  - {name: direct-b, type: direct}
proxy-groups:
  - {name: PROXY, type: select, url: "http://127.0.0.1:1/", proxies: [direct-a, direct-b]}
rules: ['MATCH,PROXY']
`, mixedPort)
}

func development(t *testing.T) *Dispatcher {
	t.Helper()
	dispatcher := New(Options{Engine: core, HostMethods: true, Development: true})
	t.Cleanup(func() {
		dispatcher.Close()
		_ = core.Stop()
	})
	return dispatcher
}

func TestGetInfo(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: unlinked{}, HostMethods: host})
		response := call(t, dispatcher, request(7, getInfo))
		if response.GetId() != 7 {
			t.Fatalf("response id = %d, want 7", response.GetId())
		}
		info := response.GetGetInfo()
		want := controlpb.Ownership_OWNERSHIP_UNSPECIFIED
		if host {
			want = controlpb.Ownership_OWNERSHIP_AVAILABLE
		}
		if info.GetOwnership() != want || info.GetCoreVersion() != "unlinked" ||
			info.GetState() != controlpb.RunState_RUN_STATE_STOPPED ||
			info.GetProtocolVersion() != product.ProtocolVersion {
			t.Fatalf("host=%v info = %v", host, info)
		}
	}
}

func TestRejectsInvalidRequests(t *testing.T) {
	dispatcher := New(Options{Engine: unlinked{}, HostMethods: true})

	wrongVersion := request(3, getInfo)
	wrongVersion.ProtocolVersion = product.ProtocolVersion + 1
	expectError(t, call(t, dispatcher, wrongVersion), 3, CodeUnsupportedVersion)

	expectError(t, call(t, dispatcher, request(0, getInfo)), 0, CodeInvalidRequest)
	expectError(t, call(t, dispatcher, request(4, func(*controlpb.Request) {})), 4, CodeInvalidRequest)

	malformed := dispatcher.Handle(context.Background(), peer.Identity{}, []byte{0xff, 0xff})
	expectError(t, decode(t, malformed), 0, CodeInvalidRequest)

	badSort := request(5, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_ListGroups{ListGroups: &controlpb.ListGroupsRequest{Sort: 42}}
	})
	expectError(t, call(t, dispatcher, badSort), 5, CodeInvalidRequest)
	badLevel := request(6, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SubscribeLogs{SubscribeLogs: &controlpb.SubscribeLogsRequest{Level: 42}}
	})
	expectError(t, call(t, dispatcher, badLevel), 6, CodeInvalidRequest)
	badPort := request(8, startRequest(profileYAML(1), &controlpb.RunOptions{MixedPort: 70000}))
	expectError(t, call(t, dispatcher, badPort), 8, CodeInvalidRequest)
	badStack := request(9, startRequest(profileYAML(1), &controlpb.RunOptions{TunStack: 42}))
	expectError(t, call(t, dispatcher, badStack), 9, CodeInvalidRequest)
}

func TestHostMethodsDependOnHost(t *testing.T) {
	extension := New(Options{Engine: core})
	t.Cleanup(func() {
		extension.Close()
		_ = core.Stop()
	})
	for id, method := range []func(*controlpb.Request){
		systemProxyRequest(false),
		func(r *controlpb.Request) { r.Method = &controlpb.Request_Claim{Claim: &controlpb.ClaimRequest{}} },
	} {
		expectError(t, call(t, extension, request(uint64(id+1), method)), uint64(id+1), CodeUnsupportedMethod)
	}
	// The extension takes its configuration through Validate, Start and Reload (D7), but
	// has no system proxy to turn on.
	expectError(t, call(t, extension, request(3, reloadRequest(profileYAML(1), nil))), 3, CodeNotRunning)
	validate := func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{Source: &controlpb.ValidateRequest_ConfigYaml{ConfigYaml: profileYAML(1)}}}
	}
	expectOK(t, call(t, extension, request(4, validate)))
	withProxy := &controlpb.RunOptions{SystemProxyEnabled: true}
	expectError(t, call(t, extension, request(5, startRequest(profileYAML(freePort(t)), withProxy))), 5, CodeUnsupportedMethod)
	expectOK(t, call(t, extension, request(6, startRequest(profileYAML(freePort(t)), nil))))
	expectOK(t, call(t, extension, request(7, stopRequest)))
	if core.State() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("state = %v after stop", core.State())
	}

	service := New(Options{Engine: core, HostMethods: true})
	expectError(t, call(t, service, request(10, systemProxyRequest(true))), 10, CodeNotImplemented)
	claim := func(r *controlpb.Request) { r.Method = &controlpb.Request_Claim{Claim: &controlpb.ClaimRequest{}} }
	expectError(t, call(t, service, request(11, claim)), 11, CodeNotImplemented)
	expectOK(t, call(t, development(t), request(12, claim)))
}

// The development server never changes the machine's network (§11): turning on the TUN
// or the system proxy fails with unsupported_method, whatever the request carries.
func TestDevelopmentRejectsTUN(t *testing.T) {
	dispatcher := development(t)
	port := freePort(t)
	tun := &controlpb.RunOptions{TunEnabled: true}
	expectError(t, call(t, dispatcher, request(1, startRequest(profileYAML(port), tun))), 1, CodeUnsupportedMethod)
	if core.State() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("state = %v after a rejected start", core.State())
	}
	expectOK(t, call(t, dispatcher, request(2, startRequest(profileYAML(port), nil))))
	expectError(t, call(t, dispatcher, request(3, reloadRequest(profileYAML(port), tun))), 3, CodeUnsupportedMethod)
	status := call(t, dispatcher, request(4, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
	})).GetGetStatus()
	if status.GetTunEnabled() || status.GetState() != controlpb.RunState_RUN_STATE_RUNNING {
		t.Fatalf("status = %v", status)
	}
}

func TestDevelopmentRejectsSystemProxy(t *testing.T) {
	dispatcher := development(t)
	expectError(t, call(t, dispatcher, request(1, systemProxyRequest(true))), 1, CodeUnsupportedMethod)
	expectOK(t, call(t, dispatcher, request(2, systemProxyRequest(false))))
	withProxy := &controlpb.RunOptions{SystemProxyEnabled: true}
	expectError(t, call(t, dispatcher, request(3, startRequest(profileYAML(freePort(t)), withProxy))), 3, CodeUnsupportedMethod)
	if core.State() != controlpb.RunState_RUN_STATE_STOPPED {
		t.Fatalf("state = %v after a rejected start", core.State())
	}
}

func TestLifecycleAndCoreMethods(t *testing.T) {
	dispatcher := development(t)
	port := freePort(t)
	profile := profileYAML(port)

	validate := func(yaml string) func(*controlpb.Request) {
		return func(r *controlpb.Request) {
			r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{Source: &controlpb.ValidateRequest_ConfigYaml{ConfigYaml: yaml}}}
		}
	}
	broken := call(t, dispatcher, request(1, validate("rules: ['MATCH,NOWHERE']\n")))
	expectError(t, broken, 1, CodeInvalidConfig)
	if message := broken.GetError().GetMessage(); strings.HasPrefix(message, "invalid configuration") || !strings.Contains(message, "NOWHERE") {
		t.Errorf("invalid_config message = %q; the UI shows mihomo's text as it is", message)
	}
	expectOK(t, call(t, dispatcher, request(2, validate(profile))))
	expectError(t, call(t, dispatcher, request(3, reloadRequest(profile, nil))), 3, CodeNotRunning)

	expectOK(t, call(t, dispatcher, request(4, startRequest(profile, nil))))
	expectError(t, call(t, dispatcher, request(5, startRequest(profile, nil))), 5, CodeAlreadyRunning)
	expectOK(t, call(t, dispatcher, request(6, reloadRequest(profile, nil))))

	groups := call(t, dispatcher, request(7, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_ListGroups{ListGroups: &controlpb.ListGroupsRequest{ExcludeNotSelectable: true}}
	})).GetListGroups().GetGroups()
	if len(groups) != 2 || groups[0].GetName() != "PROXY" {
		t.Fatalf("groups = %v", groups)
	}
	selectProxy := func(group, proxy string) func(*controlpb.Request) {
		return func(r *controlpb.Request) {
			r.Method = &controlpb.Request_SelectProxy{SelectProxy: &controlpb.SelectProxyRequest{Group: group, Proxy: proxy}}
		}
	}
	expectOK(t, call(t, dispatcher, request(8, selectProxy("PROXY", "direct-b"))))
	expectError(t, call(t, dispatcher, request(9, selectProxy("PROXY", "nowhere"))), 9, CodeInvalidRequest)
	expectError(t, call(t, dispatcher, request(10, selectProxy("", "direct-b"))), 10, CodeInvalidRequest)
	group := call(t, dispatcher, request(11, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetGroup{GetGroup: &controlpb.GetGroupRequest{Name: "PROXY"}}
	})).GetGetGroup().GetGroup()
	if group.GetNow() != "direct-b" {
		t.Fatalf("PROXY now = %q", group.GetNow())
	}
	expectOK(t, call(t, dispatcher, request(12, selectProxy("PROXY", "direct-a"))))

	expectOK(t, call(t, dispatcher, request(13, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SetMode{SetMode: &controlpb.SetModeRequest{Mode: controlpb.Mode_MODE_DIRECT}}
	})))
	status := call(t, dispatcher, request(14, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
	})).GetGetStatus()
	if status.GetMode() != controlpb.Mode_MODE_DIRECT || status.GetSystemProxyEnabled() {
		t.Fatalf("status = %v", status)
	}
	expectOK(t, call(t, dispatcher, request(15, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SetMode{SetMode: &controlpb.SetModeRequest{Mode: controlpb.Mode_MODE_RULE}}
	})))
	rules := call(t, dispatcher, request(16, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_ListRules{ListRules: &controlpb.ListRulesRequest{}}
	})).GetListRules().GetRules()
	if len(rules) != 1 || rules[0].GetProxy() != "PROXY" {
		t.Fatalf("rules = %v", rules)
	}
	expectError(t, call(t, dispatcher, request(17, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_TestDelay{TestDelay: &controlpb.TestDelayRequest{Proxy: "nowhere"}}
	})), 17, CodeInvalidRequest)
	expectOK(t, call(t, dispatcher, request(18, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_CloseAllConnections{CloseAllConnections: &controlpb.CloseAllConnectionsRequest{}}
	})))

	expectOK(t, call(t, dispatcher, request(19, stopRequest)))
	expectError(t, call(t, dispatcher, request(20, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SetMode{SetMode: &controlpb.SetModeRequest{Mode: controlpb.Mode_MODE_GLOBAL}}
	})), 20, CodeNotRunning)
}

func TestLogSubscriptionCarriesMihomoLines(t *testing.T) {
	dispatcher := development(t)
	subscribe := call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_SubscribeLogs{SubscribeLogs: &controlpb.SubscribeLogsRequest{}}
	}))
	id := subscribe.GetSubscribe().GetSubscriptionId()
	expectOK(t, call(t, dispatcher, request(2, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{Source: &controlpb.ValidateRequest_ConfigYaml{ConfigYaml: profileYAML(freePort(t))}}}
	})))
	var seen []string
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		poll := call(t, dispatcher, request(3, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_PollEvents{PollEvents: &controlpb.PollEventsRequest{SubscriptionId: id}}
		}))
		for _, event := range poll.GetPollEvents().GetEvents() {
			seen = append(seen, event.GetLog().GetMessage())
			if strings.Contains(event.GetLog().GetMessage(), "Initial configuration complete") {
				return
			}
		}
	}
	t.Fatalf("no parse line from mihomo; got %q", seen)
}

// Validate, Start and Reload all run the overrides, global first, in the order sent.
func TestOverridesReachEveryAdmission(t *testing.T) {
	dispatcher := development(t)
	profile := profileYAML(freePort(t))
	mode := func(id uint64) controlpb.Mode {
		return call(t, dispatcher, request(id, func(r *controlpb.Request) {
			r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
		})).GetGetStatus().GetMode()
	}

	broken := call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{
			Source:    &controlpb.ValidateRequest_ConfigYaml{ConfigYaml: profile},
			Overrides: []string{"mode: global\n", "- not a mapping\n"},
		}}
	}))
	expectError(t, broken, 1, CodeInvalidConfig)
	if !strings.Contains(broken.GetError().GetMessage(), "override 2 of 2") {
		t.Errorf("message = %q; it should name the override", broken.GetError().GetMessage())
	}

	expectOK(t, call(t, dispatcher, request(2, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Start{Start: &controlpb.StartRequest{
			Source:    &controlpb.StartRequest_ConfigYaml{ConfigYaml: profile},
			Overrides: []string{"mode: direct\n", "mode: global\n"},
		}}
	})))
	if got := mode(3); got != controlpb.Mode_MODE_GLOBAL {
		t.Fatalf("mode after Start = %v, want the profile override's global", got)
	}
	expectOK(t, call(t, dispatcher, request(4, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Reload{Reload: &controlpb.ReloadRequest{
			Source:    &controlpb.ReloadRequest_ConfigYaml{ConfigYaml: profile},
			Overrides: []string{"mode: direct\n"},
		}}
	})))
	if got := mode(5); got != controlpb.Mode_MODE_DIRECT {
		t.Fatalf("mode after Reload = %v, want direct", got)
	}
	// An override cannot turn on what the development server refuses.
	expectOK(t, call(t, dispatcher, request(6, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Reload{Reload: &controlpb.ReloadRequest{
			Source:    &controlpb.ReloadRequest_ConfigYaml{ConfigYaml: profile},
			Overrides: []string{"tun: {enable: true}\nallow-lan: true\n"},
		}}
	})))
	status := call(t, dispatcher, request(7, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
	})).GetGetStatus()
	if status.GetTunEnabled() || status.GetState() != controlpb.RunState_RUN_STATE_RUNNING {
		t.Fatalf("status = %v", status)
	}
}
