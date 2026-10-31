package engine

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/adapter/outbound"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/constant/features"
	"github.com/metacubex/mihomo/tunnel"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// Networks whose peers nothing ever dials: the tests replace startMesh.
func easyTierNetwork(id, name, secret string) *controlpb.MeshNetwork {
	return &controlpb.MeshNetwork{Id: id, Name: name, Network: &controlpb.MeshNetwork_Easytier{Easytier: &controlpb.EasyTierNetwork{
		NetworkName: "lab", NetworkSecret: secret, Peers: []string{"tcp://198.51.100.1:11010"}}}}
}

func zeroTierNetwork(id, name string) *controlpb.MeshNetwork {
	return &controlpb.MeshNetwork{Id: id, Name: name, Network: &controlpb.MeshNetwork_Zerotier{Zerotier: &controlpb.ZeroTierNetwork{
		NetworkId: "8056c2e21c000001"}}}
}

func meshProfile(port int, networks ...*controlpb.MeshNetwork) admission.Profile {
	composed := composition(`{"version": 1}`)
	composed.Mesh = networks
	return admission.Profile{Composition: composed, Options: admission.RunOptions{MixedPort: uint16(port)}}
}

// fakeStarts stands in for startMesh, counting the starts by outbound name and failing
// those in failing.
type fakeStarts struct {
	mu      sync.Mutex
	started []string
	failing map[string]error
	done    chan string
}

func replaceStartMesh(t *testing.T) *fakeStarts {
	t.Helper()
	fake := &fakeStarts{failing: map[string]error{}, done: make(chan string, 16)}
	original := startMesh
	startMesh = func(adapter C.ProxyAdapter) error {
		fake.mu.Lock()
		fake.started = append(fake.started, adapter.Name())
		err := fake.failing[adapter.Name()]
		fake.mu.Unlock()
		fake.done <- adapter.Name()
		return err
	}
	t.Cleanup(func() { startMesh = original })
	return fake
}

// waitStartErr waits until the engine has recorded why the outbound name did not start: the
// fake reports the start before it returns, and the engine records the failure after.
func waitStartErr(t *testing.T, engine *Engine, name string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if outbound := engine.meshOutbound(name); outbound != nil && outbound.lastStartErr() != nil {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("no failed start of %s was recorded", name)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// waitStarts waits for n starts and returns every start so far, sorted. The starts' results
// may not be recorded yet; waitStartErr waits for a failure.
func (f *fakeStarts) waitStarts(t *testing.T, n int) []string {
	t.Helper()
	for range n {
		<-f.done
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Sorted(slices.Values(f.started))
}

func running(name string) C.ProxyAdapter {
	return tunnel.Proxies()[name].Adapter()
}

// closedEasyTier says whether the EasyTier outbound inside adapter has been closed.
func closedEasyTier(t *testing.T, adapter C.ProxyAdapter) bool {
	t.Helper()
	inner, ok := unwrapOutbound(adapter).(*outbound.EasyTier)
	if !ok {
		t.Fatalf("%T is no EasyTier outbound", unwrapOutbound(adapter))
	}
	mu, err := fieldOf[sync.Mutex](inner, "startMu")
	if err != nil {
		t.Fatal(err)
	}
	closed, err := fieldOf[bool](inner, "closed")
	if err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	return *closed
}

// The pinned mihomo builds the outbounds a composition makes of the networks.
func TestNetworksAreOutboundsMihomoBuilds(t *testing.T) {
	networks := []*controlpb.MeshNetwork{easyTierNetwork("et", "实验室", "s"), zeroTierNetwork("zt", "公司")}
	if features.WithGVisor {
		networks = append(networks, &controlpb.MeshNetwork{Id: "ts", Name: "家里", Via: "proxy",
			Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{
				AuthKey: "tskey-auth-x", ControlUrl: "https://hs.example", Hostname: "laptop"}}})
	}
	admitted, err := admitProfile(meshProfile(17890, networks...), noTUN)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]C.AdapterType{"实验室": C.EasyTier, "公司": C.ZeroTier, "家里": C.Tailscale}
	for _, network := range networks {
		proxy, ok := admitted.cfg.Proxies[network.GetName()]
		if !ok || proxy.Type() != want[network.GetName()] {
			t.Errorf("%s: %v", network.GetName(), proxy)
		}
	}
	if !slices.Equal(admitted.networks, []string{"et", "zt", "ts"}[:len(networks)]) {
		t.Fatalf("networks = %v", admitted.networks)
	}
}

func TestMeshOutboundsStartAndCarryOver(t *testing.T) {
	starts := replaceStartMesh(t)
	engine := openEngine(t, admission.TunNone)
	port := porttest.Free(t)
	if _, err := engine.Start(meshProfile(port, easyTierNetwork("et1", "一", "s"), easyTierNetwork("et2", "二", "s"))); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	if got := starts.waitStarts(t, 2); !slices.Equal(got, []string{"一", "二"}) {
		t.Fatalf("started %v", got)
	}
	first, second := running("一"), running("二")

	// The same networks again: the running outbounds stay, and nothing starts.
	if _, err := engine.Reload(meshProfile(port, easyTierNetwork("et1", "一", "s"), easyTierNetwork("et2", "二", "s"))); err != nil {
		t.Fatal(err)
	}
	if running("一") != first || running("二") != second {
		t.Fatal("a reload with the same settings replaced a mesh outbound")
	}
	// GLOBAL holds the Proxy the table holds, so it reaches the running outbound too.
	global, _ := groupOf(tunnel.Proxies()["GLOBAL"])
	for _, member := range global.Proxies() {
		if member.Name() == "一" && member.Adapter() != first {
			t.Fatal("GLOBAL holds another outbound")
		}
	}
	if closedEasyTier(t, first) || closedEasyTier(t, second) {
		t.Fatal("a kept outbound was closed")
	}

	// A new secret replaces the first; the second goes with its network.
	if _, err := engine.Reload(meshProfile(port, easyTierNetwork("et1", "一", "new"))); err != nil {
		t.Fatal(err)
	}
	if got := starts.waitStarts(t, 1); !slices.Equal(got, []string{"一", "一", "二"}) {
		t.Fatalf("started %v", got)
	}
	if running("一") == first || !closedEasyTier(t, first) || !closedEasyTier(t, second) {
		t.Fatal("the replaced outbounds still run")
	}
	if _, ok := tunnel.Proxies()["二"]; ok {
		t.Fatal("the removed network is still an outbound")
	}

	last := running("一")
	if err := engine.Stop(); err != nil {
		t.Fatal(err)
	}
	if !closedEasyTier(t, last) {
		t.Fatal("Stop left a mesh outbound running")
	}
}

func TestMeshStateOfRemovedNetworksGoes(t *testing.T) {
	replaceStartMesh(t)
	engine := openEngine(t, admission.TunNone)
	root := filepath.Join(testHome, compose.MeshStateRoot)
	for _, dir := range []string{"gone", "kept", "not a network"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	// Validate never touches the state.
	if _, err := engine.Validate(meshProfile(0, easyTierNetwork("kept", "留下", "s"))); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "gone")); err != nil {
		t.Fatalf("Validate removed state: %v", err)
	}
	if _, err := engine.Start(meshProfile(porttest.Free(t), easyTierNetwork("kept", "留下", "s"))); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	entries, err := os.ReadDir(root)
	if err != nil {
		t.Fatal(err)
	}
	var left []string
	for _, entry := range entries {
		left = append(left, entry.Name())
	}
	if !slices.Equal(left, []string{"kept", "not a network"}) {
		t.Fatalf("left %v", left)
	}
}

func TestMeshStatus(t *testing.T) {
	starts := replaceStartMesh(t)
	starts.failing["公司"] = errors.New("configured ZeroTier identity collides")
	engine := openEngine(t, admission.TunNone)
	if _, err := engine.MeshStatus(context.Background()); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("MeshStatus while stopped = %v", err)
	}
	profile := meshProfile(porttest.Free(t), easyTierNetwork("et", "实验室", "s"), zeroTierNetwork("zt", "公司"))
	if _, err := engine.Start(profile); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	starts.waitStarts(t, 2)
	waitStartErr(t, engine, "公司")
	statuses, err := engine.MeshStatus(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, status := range statuses {
		got = append(got, status.Name+" "+status.Id+" "+status.Type+" "+status.State.String()+" "+status.Error)
	}
	want := []string{
		"实验室 mesh:et EasyTier MESH_STATE_STARTING ",
		"公司 mesh:zt ZeroTier MESH_STATE_FAILED configured ZeroTier identity collides",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("statuses = %q", got)
	}

	for name, wantErr := range map[string]error{"实验室": ErrInvalidArgument, "公司": ErrInvalidArgument, "nowhere": ErrInvalidArgument} {
		if _, err := engine.LoginMesh(context.Background(), name); !errors.Is(err, wantErr) {
			t.Errorf("LoginMesh(%s) = %v", name, err)
		}
	}
}

// The probes read v1.19.33's fields; a mihomo that moved one fails here first.
func TestMeshProbesFindTheirFields(t *testing.T) {
	zeroTier, err := outbound.NewZeroTier(outbound.ZeroTierOption{Name: "zt", Network: "8056c2e21c000001"})
	if err != nil {
		t.Fatal(err)
	}
	defer zeroTier.Close()
	state, err := zeroTierState(zeroTier)
	if err != nil || state.node != nil || state.joined {
		t.Fatalf("zeroTierState = %+v, %v", state, err)
	}
	status := &controlpb.MeshStatus{}
	if err := zeroTierStatus(zeroTier, status); err != nil || status.NodeId != "" {
		t.Fatalf("zeroTierStatus = %v, %v", status, err)
	}

	easyTier, err := outbound.NewEasyTier(outbound.EasyTierOption{Name: "et", NetworkName: "lab", NetworkSecret: "s",
		Peers: []string{"tcp://198.51.100.1:11010"}})
	if err != nil {
		t.Fatal(err)
	}
	defer easyTier.Close()
	if instance, err := easyTierInstance(easyTier); err != nil || instance != nil {
		t.Fatalf("easyTierInstance = %v, %v", instance, err)
	}
	// mihomo wraps each outbound it parses.
	if unwrapOutbound(outbound.NewAutoCloseProxyAdapter(easyTier)) != C.ProxyAdapter(easyTier) {
		t.Fatal("unwrapOutbound did not find the outbound")
	}

	if _, err := fieldOf[int](easyTier, "mu"); err == nil {
		t.Fatal("a field of another type was read")
	}
	if _, err := fieldOf[int](easyTier, "nothing"); err == nil {
		t.Fatal("a missing field was read")
	}
}

func TestDescribeZeroTier(t *testing.T) {
	assigned := []string{"10.147.17.5/24"}
	cases := []struct {
		state zeroTierSnapshot
		want  string
	}{
		{zeroTierSnapshot{authURL: "https://my.zerotier.com/sso"}, "MESH_STATE_NEEDS_LOGIN https://my.zerotier.com/sso  0"},
		{zeroTierSnapshot{networkErr: errors.New("ZeroTier network access denied")}, "MESH_STATE_WAITING   0"},
		{zeroTierSnapshot{networkErr: errors.New("ZeroTier network not found or controller unsupported")},
			"MESH_STATE_FAILED  ZeroTier network not found or controller unsupported 0"},
		{zeroTierSnapshot{}, "MESH_STATE_STARTING   0"},
	}
	for _, c := range cases {
		status := &controlpb.MeshStatus{State: controlpb.MeshState_MESH_STATE_STARTING}
		describeZeroTier(c.state, "a1b2c3d4e5", nil, status)
		got := status.State.String() + " " + status.LoginUrl + " " + status.Error + " " + string(rune('0'+status.PeerCount))
		if got != c.want || status.NodeId != "a1b2c3d4e5" {
			t.Errorf("%+v: %q", c.state, got)
		}
	}
	joined := zeroTierSnapshot{joined: true}
	joined.config.Name = "office"
	for _, a := range assigned {
		joined.config.Assigned = append(joined.config.Assigned, mustPrefix(t, a))
	}
	status := &controlpb.MeshStatus{}
	describeZeroTier(joined, "a1b2c3d4e5", zeroTierPeers, status)
	if status.State != controlpb.MeshState_MESH_STATE_ONLINE || status.NetworkName != "office" ||
		!slices.Equal(status.Addresses, assigned) || status.PeerCount != 1 {
		t.Fatalf("joined: %v", status)
	}
}

func TestEasyTierAddress(t *testing.T) {
	for text, want := range map[string]string{"10.144.144.1/24": "10.144.144.1/24", "10.144.144.1": "10.144.144.1/32", "": ""} {
		if got := easyTierAddress(text); got != want {
			t.Errorf("easyTierAddress(%q) = %q", text, got)
		}
	}
}

// A subscription's mesh outbounds start at their first connection, as mihomo has them; only
// the networks the user added start at once. And a subscription's EasyTier outbound never
// listens for peers (docs/mesh.md §4).
func TestSubscriptionMeshOutboundsWaitForTraffic(t *testing.T) {
	starts := replaceStartMesh(t)
	engine := openEngine(t, admission.TunNone)
	profile := meshProfile(porttest.Free(t), easyTierNetwork("et", "实验室", "s"))
	profile.Composition.Sources = append(profile.Composition.Sources, compose.Source{ID: "m", Name: "M", YAML: []byte(`
proxies:
  - {name: 公司, type: zerotier, network: 8056c2e21c000001}
  - {name: 机房, type: easytier, network-name: n, network-secret: s, peers: ['tcp://198.51.100.1:11010'],
     no-listener: false, listeners: ['tcp://0.0.0.0:11010']}
`)})
	report, err := engine.Start(profile)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	if got := starts.waitStarts(t, 1); !slices.Equal(got, []string{"实验室"}) {
		t.Fatalf("started %v", got)
	}
	select {
	case name := <-starts.done:
		t.Fatalf("%s started without a connection", name)
	case <-time.After(200 * time.Millisecond):
	}
	var restricted []string
	for _, warning := range report.GetWarnings() {
		if warning.GetReason() == compose.ReasonMeshRestricted {
			restricted = append(restricted, warning.GetEntryId()+" "+warning.GetTarget())
		}
	}
	if want := []string{"mesh:m/机房 listeners", "mesh:m/机房 no-listener"}; !slices.Equal(restricted, want) {
		t.Fatalf("restricted %v", restricted)
	}
	// Still a status for each: the lazy ones as starting until something uses them.
	statuses, err := engine.MeshStatus(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(statuses) != 3 {
		t.Fatalf("statuses = %v", statuses)
	}
}

// MeshStatus runs beside reloads: the go test -race of the mesh tests watches it.
func TestMeshStatusDuringReloads(t *testing.T) {
	replaceStartMesh(t)
	engine := openEngine(t, admission.TunNone)
	port := porttest.Free(t)
	if _, err := engine.Start(meshProfile(port, easyTierNetwork("et", "实验室", "s"))); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = engine.Stop() })
	done := make(chan struct{})
	var wait sync.WaitGroup
	wait.Go(func() {
		for {
			select {
			case <-done:
				return
			default:
				if _, err := engine.MeshStatus(context.Background()); err != nil {
					t.Error(err)
					return
				}
			}
		}
	})
	for range 5 {
		if _, err := engine.Reload(meshProfile(port, easyTierNetwork("et", "实验室", "s"))); err != nil {
			t.Fatal(err)
		}
	}
	close(done)
	wait.Wait()
}
