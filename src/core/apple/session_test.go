//go:build cgo

package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/operation"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
	"google.golang.org/protobuf/proto"
)

func writeFiles(t *testing.T, dir string, files map[string]string) {
	t.Helper()
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func readDir(t *testing.T, dir string) map[string]string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string]string{}
	for _, entry := range entries {
		content, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		files[entry.Name()] = string(content)
	}
	return files
}

func TestAttachSeedsTheHomeBeforeOpeningTheEngine(t *testing.T) {
	resources := t.TempDir()
	files := map[string]string{
		"GeoSite.dat": "site", "GeoIP.dat": "ip", "geoip.metadb": "mmdb", "ASN.mmdb": "asn",
	}
	writeFiles(t, resources, files)
	home := t.TempDir()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	if err := attach(int(reader.Fd()), home, resources, false); err == nil {
		detach()
		t.Fatal("attached a pipe as the TUN")
	}
	got := readDir(t, home)
	for name, content := range files {
		if got[name] != content {
			t.Fatalf("home[%q] = %q, want %q", name, got[name], content)
		}
	}
}

// These tests never attach a real TUN; the fd mode on one runs in CI
// (internal/engine/tun_fd_linux_test.go).

func TestNetworkIsTheFDNetwork(t *testing.T) {
	var got network
	if err := json.Unmarshal(networkJSON(), &got); err != nil {
		t.Fatal(err)
	}
	want := network{
		IPv4Address:      "198.18.0.1",
		IPv4SubnetMask:   "255.255.255.252",
		IPv6Address:      "fdfe:dcba:9876::1",
		IPv6PrefixLength: 126,
		DNSServer:        admission.FDNetwork.DNSServer().String(),
		MTU:              admission.FDNetwork.MTU,
	}
	if got != want || got.DNSServer != "198.18.0.2" {
		t.Fatalf("network = %+v, want %+v", got, want)
	}
}

func getInfo(t *testing.T) *controlpb.Response {
	t.Helper()
	request, err := proto.Marshal(&controlpb.Request{
		ProtocolVersion: product.ProtocolVersion,
		Id:              1,
		Method:          &controlpb.Request_GetInfo{GetInfo: &controlpb.GetInfoRequest{}},
	})
	if err != nil {
		t.Fatal(err)
	}
	var response controlpb.Response
	if err := proto.Unmarshal(handle(request), &response); err != nil {
		t.Fatal(err)
	}
	return &response
}

func TestHandleWithoutATunnel(t *testing.T) {
	if code := getInfo(t).GetError().GetCode(); code != control.CodeNotRunning {
		t.Fatalf("code = %q, want %q", code, control.CodeNotRunning)
	}
}

func TestAttachRefusesADescriptorThatIsNotATUN(t *testing.T) {
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	if err := attach(int(reader.Fd()), t.TempDir(), "", false); err == nil {
		detach()
		t.Fatal("attached a pipe as the TUN")
	}
	if code := getInfo(t).GetError().GetCode(); code != control.CodeNotRunning {
		t.Fatalf("code = %q after a refused attach", code)
	}
	detach() // nothing attached: does nothing
}

func TestInitialConfigurationGuard(t *testing.T) {
	for _, mode := range []string{"abandoned", "configured", "detached"} {
		t.Run(mode, func(t *testing.T) {
			failed := make(chan operation.Failure, 1)
			_, done := guardInitialConfiguration(5*time.Millisecond, func() bool {
				return mode == "configured"
			}, func(f operation.Failure) { failed <- f })
			defer done()
			if mode == "detached" {
				done()
			}
			wait := 50 * time.Millisecond
			if mode == "abandoned" {
				wait = 2 * time.Second
			}
			select {
			case failure := <-failed:
				if mode != "abandoned" || failure.Method != "await_initial_configuration" {
					t.Fatal(mode, failure)
				}
			case <-time.After(wait):
				if mode == "abandoned" {
					t.Fatal("empty tunnel was not timed out")
				}
			}
		})
	}
}

func TestPreparationActivityRefreshesTheIdleBudget(t *testing.T) {
	failed := make(chan operation.Failure, 1)
	touch, done := guardInitialConfiguration(time.Second, func() bool { return false },
		func(f operation.Failure) { failed <- f })
	defer done()
	time.Sleep(600 * time.Millisecond)
	touch()
	select {
	case <-failed:
		t.Fatal("active preparation used the original deadline")
	case <-time.After(600 * time.Millisecond):
	}
	select {
	case <-failed: // the UI disappeared after its last message
	case <-time.After(2 * time.Second):
		t.Fatal("abandoned preparation did not expire")
	}
}
