//go:build cgo

package main

import (
	"encoding/json"
	"os"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

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
		DNSServer:        engine.FDNetwork.DNSServer().String(),
		MTU:              engine.FDNetwork.MTU,
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
	if err := attach(int(reader.Fd()), t.TempDir()); err == nil {
		detach()
		t.Fatal("attached a pipe as the TUN")
	}
	if code := getInfo(t).GetError().GetCode(); code != control.CodeNotRunning {
		t.Fatalf("code = %q after a refused attach", code)
	}
	detach() // nothing attached: does nothing
}
