package owner

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/sysproxy"
)

const (
	alice = "S-1-5-21-1004336348-1177238915-682003330-1001"
	bob   = "S-1-5-21-1004336348-1177238915-682003330-1002"
)

// machine stands in for Windows: who is signed in to which session, each user's proxy
// settings, and the service restarts asked for.
type machine struct {
	secured  map[string]string
	sessions map[uint32]string
	proxies  map[string]sysproxy.Settings
	restarts int
}

func newMachine() *machine {
	return &machine{secured: map[string]string{}, sessions: map[uint32]string{}, proxies: map[string]sysproxy.Settings{}}
}

func (m *machine) SecureDir(dir, reader string) error {
	m.secured[dir] = reader
	return os.MkdirAll(dir, 0o700)
}

func (m *machine) SessionUser(session uint32) (string, error) {
	if sid, ok := m.sessions[session]; ok {
		return sid, nil
	}
	return "", fmt.Errorf("no session %d", session)
}

func (m *machine) SignedIn(sid string) []uint32 {
	var sessions []uint32
	for session, user := range m.sessions {
		if user == sid {
			sessions = append(sessions, session)
		}
	}
	slices.Sort(sessions)
	return sessions
}

func (m *machine) ReadProxy(session uint32) (sysproxy.Settings, error) {
	sid, err := m.SessionUser(session)
	return m.proxies[sid], err
}

func (m *machine) WriteProxy(session uint32, settings sysproxy.Settings) error {
	sid, err := m.SessionUser(session)
	if err == nil {
		m.proxies[sid] = settings
	}
	return err
}

func (m *machine) Restart() { m.restarts++ }

func (m *machine) NetworkReady() bool { return true }

// fakeCore is the dispatcher's side: Resume and Suspend report back as the dispatcher does.
type fakeCore struct {
	host    *Service
	running bool
	resumed []*controlpb.StartRequest
	stops   int // a take-over's stops
	stopErr error
}

func (c *fakeCore) Running() bool { return c.running }

// stop is what the dispatcher hands TakeOver: the engine's Stop, under the lock it holds.
func (c *fakeCore) stop() error {
	c.stops++
	if c.stopErr != nil {
		return c.stopErr
	}
	c.running = false
	return nil
}

func (c *fakeCore) Resume(start *controlpb.StartRequest) error {
	c.running = true
	c.resumed = append(c.resumed, start)
	return c.host.Applied(start)
}

func (c *fakeCore) Suspend() error {
	c.running = false
	return c.host.Suspended()
}

func (c *fakeCore) ResumeContext(ctx context.Context, start *controlpb.StartRequest) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return c.Resume(start)
}

func (c *fakeCore) SuspendContext(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return c.Suspend()
}

// bundled is the GEO data an installer ships, in a directory of its own.
func bundled(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for name, content := range map[string]string{"GeoSite.dat": "bundled site", "GeoIP.dat": "bundled ip"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// start runs the service as a new process would: Open, then Attach.
func start(t *testing.T, root string, m *machine) (*Service, *fakeCore, string) {
	t.Helper()
	service, home, err := Open(context.Background(), root, bundled(t), m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	core := &fakeCore{host: service}
	service.Attach(core)
	return service, core, home
}

// geoData is what home holds of the GEO data, by file name.
func geoData(t *testing.T, home string) map[string]string {
	t.Helper()
	found := map[string]string{}
	for _, name := range []string{"GeoSite.dat", "GeoIP.dat"} {
		content, err := os.ReadFile(filepath.Join(home, name))
		if err == nil {
			found[name] = string(content)
		} else if !errors.Is(err, fs.ErrNotExist) {
			t.Fatal(err)
		}
	}
	return found
}

func as(sid string, session uint32) peer.Identity {
	return peer.Identity{UserID: sid, SessionID: session}
}

// elevated is sid with an administrator's rights, as the take-over helper runs.
func elevated(sid string, session uint32) peer.Identity {
	return peer.Identity{UserID: sid, SessionID: session, Administrator: true}
}

func profile(systemProxy bool) *controlpb.StartRequest {
	return &controlpb.StartRequest{
		Source:    &controlpb.StartRequest_ConfigYaml{ConfigYaml: "mixed-port: 7890\n"},
		Overrides: []string{"mode: rule\n"},
		Options:   &controlpb.RunOptions{SystemProxyEnabled: systemProxy, MixedPort: 7890, TunEnabled: true},
	}
}

func TestValidSID(t *testing.T) {
	for _, sid := range []string{alice, "S-1-5-18", "S-1-5-32-544"} {
		if !ValidSID(sid) {
			t.Errorf("ValidSID(%q) = false", sid)
		}
	}
	for _, sid := range []string{"", "S-1-", "S-1-5-", "S-1-5--1", `S-1-5-21\..\x`, "S-1-5-21/1", "s-1-5-18", "S-1-5-18 ", "S-1-99999999999"} {
		if ValidSID(sid) {
			t.Errorf("ValidSID(%q) = true", sid)
		}
	}
}

// A process opens mihomo in one owner's home for its whole life (§3.2): the first claim,
// and any claim by someone else, records the new owner and restarts the service.
func TestClaimRestartsForANewOwner(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	service, _, home := start(t, root, m)
	if home != "" || m.secured[root] != "" || m.secured[filepath.Join(root, usersDir)] != "" {
		t.Fatalf("home %q, secured %v", home, m.secured)
	}
	if users, err := os.ReadDir(filepath.Join(root, usersDir)); err != nil || len(users) != 0 {
		t.Fatalf("users = %v, %v", users, err)
	}
	if got := service.Ownership(as(alice, 1)); got != controlpb.Ownership_OWNERSHIP_AVAILABLE {
		t.Fatalf("ownership = %v", got)
	}
	if err := service.Admit(as(alice, 1)); !errors.Is(err, control.ErrNotClaimed) {
		t.Fatalf("admit before a claim: %v", err)
	}
	if err := service.Claim(as(alice, 1)); !errors.Is(err, control.ErrRestarting) || m.restarts != 1 {
		t.Fatalf("claim = %v after %d restarts", err, m.restarts)
	}
	if err := service.Admit(as(alice, 1)); !errors.Is(err, control.ErrRestarting) {
		t.Fatalf("admit while restarting: %v", err)
	}
	if err := service.Claim(as(alice, 1)); !errors.Is(err, control.ErrRestarting) || m.restarts != 1 {
		t.Fatalf("second claim = %v after %d restarts", err, m.restarts)
	}

	service, _, home = start(t, root, m)
	if home != Home(root, alice) || m.secured[home] != alice {
		t.Fatalf("home %q, secured %v", home, m.secured)
	}
	if got := geoData(t, home); len(got) != 2 || got["GeoSite.dat"] != "bundled site" || got["GeoIP.dat"] != "bundled ip" {
		t.Fatalf("GEO data = %v", got)
	}
	if err := service.Admit(as(alice, 1)); err != nil {
		t.Fatal(err)
	}
	if got := service.Ownership(as(alice, 1)); got != controlpb.Ownership_OWNERSHIP_CALLER {
		t.Fatalf("owner's ownership = %v", got)
	}
	if got := service.Ownership(as(bob, 2)); got != controlpb.Ownership_OWNERSHIP_AVAILABLE {
		t.Fatalf("other user's ownership = %v", got)
	}
	if err := service.Admit(as(bob, 2)); !errors.Is(err, control.ErrNotClaimed) {
		t.Fatalf("admit another user: %v", err)
	}
	// The owner claiming from another session moves the session the service uses.
	if err := service.Claim(as(alice, 3)); err != nil || m.restarts != 1 {
		t.Fatalf("owner's claim = %v after %d restarts", err, m.restarts)
	}
	if record, _, _ := LoadRecord(root); record != (Record{SID: alice, Session: 3}) {
		t.Fatalf("record = %+v", record)
	}
	if err := service.Claim(as("not a SID", 2)); err == nil {
		t.Fatal("claimed without a SID")
	}
}

// The home keeps the GEO data the engine's updates wrote there (docs/routing.md §8.4), and
// a service without the bundled data still opens it: mihomo downloads what a rule needs.
func TestOpenSeedsWhatTheHomeLacks(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	home := Home(root, alice)
	if err := os.MkdirAll(home, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "GeoIP.dat"), []byte("updated ip"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, opened := start(t, root, m); opened != home {
		t.Fatalf("home = %q", opened)
	}
	if got := geoData(t, home); len(got) != 2 || got["GeoSite.dat"] != "bundled site" || got["GeoIP.dat"] != "updated ip" {
		t.Fatalf("GEO data = %v", got)
	}

	root = t.TempDir()
	if err := SaveRecord(root, Record{SID: bob, Session: 2}); err != nil {
		t.Fatal(err)
	}
	_, opened, err := Open(context.Background(), root, filepath.Join(t.TempDir(), "missing"), m, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil || opened != Home(root, bob) {
		t.Fatalf("home = %q, %v", opened, err)
	}
	if got := geoData(t, opened); len(got) != 0 {
		t.Fatalf("GEO data = %v", got)
	}
}

func TestOtherUserWaitsForTheTunnel(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	service, core, _ := start(t, root, m)
	core.running = true
	if got := service.Ownership(as(bob, 2)); got != controlpb.Ownership_OWNERSHIP_OTHER {
		t.Fatalf("ownership = %v", got)
	}
	if err := service.Admit(as(bob, 2)); !errors.Is(err, control.ErrOwnedByOtherUser) {
		t.Fatalf("admit = %v", err)
	}
	if err := service.Claim(as(bob, 2)); !errors.Is(err, control.ErrOwnedByOtherUser) || m.restarts != 0 {
		t.Fatalf("claim = %v after %d restarts", err, m.restarts)
	}
	core.running = false
	if err := service.Claim(as(bob, 2)); !errors.Is(err, control.ErrRestarting) || m.restarts != 1 {
		t.Fatalf("claim of a stopped tunnel = %v after %d restarts", err, m.restarts)
	}
	if _, _, home := start(t, root, m); home != Home(root, bob) {
		t.Fatalf("home = %q", home)
	}
}

// The tunnel comes back when its owner is signed in: at once when the service starts
// while they are, otherwise at their next sign-in, and never for another user's (D5).
func TestTunnelComesBackForItsOwner(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	service, core, home := start(t, root, m)
	core.running = true
	if err := service.Applied(profile(false)); err != nil {
		t.Fatal(err)
	}
	if !(kept{home}).wasRunning() {
		t.Fatal("was_running is not kept")
	}

	// The machine restarts: nobody is signed in when the service starts.
	m.sessions = map[uint32]string{}
	service, core, _ = start(t, root, m)
	if len(core.resumed) != 0 {
		t.Fatal("resumed before the owner signed in")
	}
	m.sessions[2] = bob
	service.SignedIn(2)
	if len(core.resumed) != 0 {
		t.Fatal("resumed for another user")
	}
	m.sessions[4] = alice
	service.SignedIn(4)
	if len(core.resumed) != 1 || !proto.Equal(core.resumed[0], profile(false)) {
		t.Fatalf("resumed %v", core.resumed)
	}
	if record, _, _ := LoadRecord(root); record.Session != 4 {
		t.Fatalf("record = %+v", record)
	}

	// Signing out of another session leaves the tunnel; signing out of the owner's stops it
	// and keeps it coming back.
	service.SignedOut(2)
	if !core.running {
		t.Fatal("stopped at another user's sign-out")
	}
	delete(m.sessions, 4)
	service.SignedOut(4)
	if core.running || !(kept{Home(root, alice)}).wasRunning() {
		t.Fatalf("running %v after the owner signed out", core.running)
	}
	m.sessions[5] = alice
	service.SignedIn(5)
	if len(core.resumed) != 2 {
		t.Fatalf("resumed %d times", len(core.resumed))
	}

	// A service restart while the owner is signed in brings it back at once; after Stop,
	// nothing does.
	_, core, _ = start(t, root, m)
	if len(core.resumed) != 1 {
		t.Fatal("not resumed at service start")
	}
	core.running = false
	if err := core.host.Stopped(); err != nil {
		t.Fatal(err)
	}
	if _, core, _ = start(t, root, m); len(core.resumed) != 0 {
		t.Fatal("resumed after Stop")
	}
}

func TestStopIntentIsKeptBeforeTheCoreCanStop(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	m.sessions[1], m.sessions[2] = alice, bob
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	service, core, home := start(t, root, m)
	core.running = true
	if err := service.Applied(profile(false)); err != nil {
		t.Fatal(err)
	}
	if err := service.StopRequested(as(bob, 2)); !errors.Is(err, control.ErrOwnedByOtherUser) {
		t.Fatalf("other user stopped the owner: %v", err)
	}
	if !service.ResumeAllowed() {
		t.Fatal("the other user's refusal changed the owner's intent")
	}
	if err := service.StopRequested(as(alice, 1)); err != nil {
		t.Fatal(err)
	}
	if !core.running || (kept{home}).wasRunning() || service.ResumeAllowed() {
		t.Fatal("stop intent was not kept independently of core cleanup")
	}
	// The core may be stuck, so recovery replaces the process instead of stopping it.
	_, restarted, _ := start(t, root, m)
	if restarted.running || len(restarted.resumed) != 0 {
		t.Fatal("service recovery undid the user's disconnect")
	}
}

func TestSystemProxyIsTheOwners(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	theirs := sysproxy.Settings{Flags: sysproxy.Direct | sysproxy.AutoDetect, AutoConfigURL: "http://wpad/wpad.dat"}
	m.sessions[1], m.proxies[alice] = alice, theirs
	m.sessions[2], m.proxies[bob] = bob, sysproxy.Settings{Flags: sysproxy.Direct}
	service, core, home := start(t, root, m)

	if err := service.SetSystemProxy(true); !errors.Is(err, engine.ErrNotRunning) {
		t.Fatalf("set while stopped: %v", err)
	}
	core.running = true
	if err := service.Applied(profile(true)); err != nil {
		t.Fatal(err)
	}
	if m.proxies[alice] != sysproxy.Core(7890) || !service.SystemProxyEnabled() {
		t.Fatalf("alice's proxy = %+v", m.proxies[alice])
	}
	if m.proxies[bob] != (sysproxy.Settings{Flags: sysproxy.Direct}) {
		t.Fatalf("bob's proxy = %+v", m.proxies[bob])
	}
	if err := service.SetSystemProxy(false); err != nil {
		t.Fatal(err)
	}
	if m.proxies[alice] != theirs || service.SystemProxyEnabled() {
		t.Fatalf("alice's proxy = %+v after turning it off", m.proxies[alice])
	}
	if saved, _ := (kept{home}).profile(); saved.GetOptions().GetSystemProxyEnabled() || !(kept{home}).wasRunning() {
		t.Fatalf("kept %v", saved)
	}
	if err := service.SetSystemProxy(true); err != nil {
		t.Fatal(err)
	}

	// Signing out leaves the service unable to write the owner's settings; their next
	// sign-in brings the tunnel back over the settings it kept, so Stop restores theirs and
	// not the service's own.
	delete(m.sessions, 1)
	service.SignedOut(1)
	if _, saved, _ := (kept{home}).previousProxy(); !saved || service.SystemProxyEnabled() {
		t.Fatalf("saved %v, enabled %v after sign-out", saved, service.SystemProxyEnabled())
	}
	m.sessions[3] = alice
	service.SignedIn(3)
	if !core.running || m.proxies[alice] != sysproxy.Core(7890) {
		t.Fatalf("running %v, proxy %+v after sign-in", core.running, m.proxies[alice])
	}
	// The service stopping gives them back too, and its next start takes them again.
	service.Close()
	if m.proxies[alice] != theirs || !(kept{home}).wasRunning() {
		t.Fatalf("proxy %+v after the service stopped", m.proxies[alice])
	}
	service, core, _ = start(t, root, m)
	if !core.running || m.proxies[alice] != sysproxy.Core(7890) {
		t.Fatalf("running %v, proxy %+v after the service started", core.running, m.proxies[alice])
	}
	core.running = false
	if err := service.Stopped(); err != nil {
		t.Fatal(err)
	}
	if _, saved, _ := (kept{home}).previousProxy(); saved || m.proxies[alice] != theirs {
		t.Fatalf("saved %v, proxy %+v after Stop", saved, m.proxies[alice])
	}
}

// A former owner whose settings could not be restored before someone else claimed the
// service gets them back at their next sign-in.
func TestFormerOwnerGetsTheirProxyBack(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	theirs := sysproxy.Settings{Flags: sysproxy.Direct}
	m.sessions[1], m.proxies[alice] = alice, theirs
	service, core, _ := start(t, root, m)
	core.running = true
	if err := service.Applied(profile(true)); err != nil {
		t.Fatal(err)
	}
	delete(m.sessions, 1)
	service.SignedOut(1)
	m.sessions[2] = bob
	if err := service.Claim(as(bob, 2)); !errors.Is(err, control.ErrRestarting) {
		t.Fatal(err)
	}
	service, _, _ = start(t, root, m)
	m.sessions[3] = alice
	service.SignedIn(3)
	if m.proxies[alice] != theirs {
		t.Fatalf("alice's proxy = %+v", m.proxies[alice])
	}
	if _, saved, _ := (kept{Home(root, alice)}).previousProxy(); saved {
		t.Fatal("alice's settings are still kept")
	}
}

// An administrator takes over another user's running tunnel (decision D5b): it stops as
// after Stop, its owner gets their proxy settings back, and the service restarts for the
// administrator. Anyone else still waits.
func TestAdministratorTakesOver(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	theirs := sysproxy.Settings{Flags: sysproxy.Direct}
	m.sessions[1], m.proxies[alice] = alice, theirs
	m.sessions[2] = bob
	service, core, home := start(t, root, m)
	core.running = true
	if err := service.Applied(profile(true)); err != nil {
		t.Fatal(err)
	}

	if err := service.TakeOver(as(bob, 2), core.stop); !errors.Is(err, control.ErrOwnedByOtherUser) {
		t.Fatalf("a take-over without an administrator's rights: %v", err)
	}
	if core.stops != 0 || !core.running || m.restarts != 0 || m.proxies[alice] != sysproxy.Core(7890) {
		t.Fatalf("%d stops, running %v, %d restarts", core.stops, core.running, m.restarts)
	}
	core.stopErr = errors.New("stuck")
	if err := service.TakeOver(elevated(bob, 2), core.stop); err == nil || errors.Is(err, control.ErrRestarting) {
		t.Fatalf("a take-over that could not stop the tunnel: %v", err)
	}
	if record, _, _ := LoadRecord(root); record.SID != alice || m.restarts != 0 {
		t.Fatalf("record %+v after %d restarts", record, m.restarts)
	}
	core.stopErr = nil
	if err := service.TakeOver(elevated(bob, 2), core.stop); !errors.Is(err, control.ErrRestarting) {
		t.Fatalf("take-over = %v", err)
	}
	if core.stops != 2 || core.running || m.restarts != 1 {
		t.Fatalf("%d stops, running %v, %d restarts", core.stops, core.running, m.restarts)
	}
	if m.proxies[alice] != theirs || (kept{home}).wasRunning() {
		t.Fatalf("alice's proxy %+v, was_running %v", m.proxies[alice], (kept{home}).wasRunning())
	}
	if _, saved, _ := (kept{home}).previousProxy(); saved {
		t.Fatal("alice's settings are still kept")
	}
	if record, _, _ := LoadRecord(root); record != (Record{SID: bob, Session: 2}) {
		t.Fatalf("record = %+v", record)
	}
	if err := service.Admit(as(alice, 1)); !errors.Is(err, control.ErrRestarting) {
		t.Fatalf("admit the former owner while restarting: %v", err)
	}

	// The new process serves bob; alice's UI sees his tunnel the usual way, and hers does
	// not come back when she signs in again.
	service, core, home = start(t, root, m)
	if home != Home(root, bob) {
		t.Fatalf("home = %q", home)
	}
	if err := service.Admit(as(bob, 2)); err != nil {
		t.Fatal(err)
	}
	core.running = true
	if err := service.Admit(as(alice, 1)); !errors.Is(err, control.ErrOwnedByOtherUser) {
		t.Fatalf("admit the former owner: %v", err)
	}
	m.sessions[3] = alice
	service.SignedIn(3)
	if len(core.resumed) != 0 {
		t.Fatalf("resumed %v", core.resumed)
	}
	// With nothing to take over, a take-over is a claim: of a stopped tunnel, or of one's own.
	if err := service.TakeOver(elevated(bob, 4), core.stop); err != nil || core.stops != 0 {
		t.Fatalf("the owner's take-over = %v after %d stops", err, core.stops)
	}
	core.running = false
	if err := service.TakeOver(elevated(alice, 3), core.stop); !errors.Is(err, control.ErrRestarting) || core.stops != 0 {
		t.Fatalf("a take-over of a stopped tunnel = %v after %d stops", err, core.stops)
	}
}

// A former owner who is not signed in when an administrator takes over gets their proxy
// settings back at their next sign-in, as after signing out (D5).
func TestTakeOverKeepsTheProxyForTheNextSignIn(t *testing.T) {
	root, m := t.TempDir(), newMachine()
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	theirs := sysproxy.Settings{Flags: sysproxy.Direct | sysproxy.AutoDetect}
	m.sessions[1], m.proxies[alice] = alice, theirs
	service, core, home := start(t, root, m)
	core.running = true
	if err := service.Applied(profile(true)); err != nil {
		t.Fatal(err)
	}
	// Her session went away without a sign-out the service heard of.
	delete(m.sessions, 1)
	m.sessions[2] = bob
	if err := service.TakeOver(elevated(bob, 2), core.stop); !errors.Is(err, control.ErrRestarting) {
		t.Fatalf("take-over = %v", err)
	}
	if _, saved, _ := (kept{home}).previousProxy(); !saved || m.proxies[alice] != sysproxy.Core(7890) {
		t.Fatalf("saved %v, alice's proxy %+v", saved, m.proxies[alice])
	}
	service, _, _ = start(t, root, m)
	m.sessions[3] = alice
	service.SignedIn(3)
	if m.proxies[alice] != theirs {
		t.Fatalf("alice's proxy = %+v", m.proxies[alice])
	}
	if _, saved, _ := (kept{home}).previousProxy(); saved {
		t.Fatal("alice's settings are still kept")
	}
}
