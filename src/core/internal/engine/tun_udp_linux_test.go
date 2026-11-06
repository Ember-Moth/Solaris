//go:build linux && tuntest

package engine

// UDP through the TUN, the way games and QUIC take it: the owned-TUN and fd-mode tests send
// datagrams from this process to SOLARIS_TUN_UDP_TARGET, an echo server in md-srv that the
// routes reach through the TUN, first DIRECT and then through a node. The sizes go past the
// veth's MTU and past the TUN's own, so IP fragments meet mihomo's stack both ways.

import (
	"net"
	"net/netip"
	"os"
	"slices"
	"syscall"
	"testing"
	"time"

	"github.com/metacubex/mihomo/common/yaml"
	"github.com/metacubex/mihomo/listener"
	"golang.org/x/sys/unix"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
)

func udpTarget(t *testing.T) netip.AddrPort {
	t.Helper()
	value := os.Getenv("SOLARIS_TUN_UDP_TARGET")
	if value == "" {
		t.Fatal("SOLARIS_TUN_UDP_TARGET names the UDP echo server outside the namespace")
	}
	target, err := netip.ParseAddrPort(value)
	if err != nil {
		t.Fatalf("SOLARIS_TUN_UDP_TARGET: %v", err)
	}
	return target
}

// tunUDPSizes are a byte, QUIC's initial datagram, a full Ethernet frame, more than the veth
// carries in one frame, the most the TUN carries in one packet, and more than that.
func tunUDPSizes() []int {
	mtu := listener.GetTunConf().MTU
	if mtu == 0 {
		mtu = 9000 // sing_tun's default
	}
	whole := int(mtu) - 20 - 8
	return []int{1, 1200, 1472, 4000, whole, whole + 1000}
}

// udpThroughTUN exchanges every size with the echo server from a socket of this process,
// which the routes send into the TUN, and checks that mihomo carried the datagrams.
func udpThroughTUN(t *testing.T, engine *Engine, target netip.AddrPort) {
	t.Helper()
	conn, err := net.DialUDP("udp4", nil, net.UDPAddrFromAddrPort(target))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	before := engine.Traffic()
	buffer := make([]byte, 65535)
	send := func(payload []byte) error {
		_, err := conn.Write(payload)
		return err
	}
	receive := func(deadline time.Time) ([]byte, error) {
		_ = conn.SetReadDeadline(deadline)
		n, err := conn.Read(buffer)
		return buffer[:n], err
	}
	for i, size := range tunUDPSizes() {
		if err := echoed(uint64(i+1), size, 3, send, receive); err != nil {
			t.Errorf("UDP to %s through the TUN: %v", target, err)
		}
	}
	for deadline := time.Now().Add(5 * time.Second); ; {
		after := engine.Traffic()
		if after.GetDownTotalBytes() > before.GetDownTotalBytes() && after.GetUpTotalBytes() > before.GetUpTotalBytes() {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("mihomo carried no UDP: %v -> %v", before, after)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// bindToDevice binds a socket to the named link, as mihomo binds its own dials, so the
// node's relay leaves past the TUN.
func bindToDevice(name string) func(network, address string, c syscall.RawConn) error {
	return func(_, _ string, c syscall.RawConn) error {
		var err error
		if controlErr := c.Control(func(fd uintptr) { err = unix.BindToDevice(int(fd), name) }); controlErr != nil {
			return controlErr
		}
		return err
	}
}

// udpThroughTUNNode reloads the engine onto a node and exchanges UDP through the TUN again:
// an override sends everything in profile through the node. The node runs in this process
// on loopback, which mihomo reaches over lo whatever the TUN routes, and relays to the echo
// server over outbound, as a real node relays from its own network.
func udpThroughTUNNode(t *testing.T, engine *Engine, target netip.AddrPort, outbound string, profile admission.Profile) {
	t.Helper()
	tunnel := &nodeTunnel{remote: netip.PrefixFrom(target.Addr(), 32), control: bindToDevice(outbound)}
	node := startNode(t, shadowsocksNode(), tunnel)
	node["interface-name"] = "lo"
	override, err := yaml.Marshal(map[string]any{
		"proxies":      []any{node},
		"proxy-groups": []any{map[string]any{"name": "PROXY", "type": "select", "proxies": []any{"node"}}},
		"rules":        []any{"MATCH,PROXY"},
	})
	if err != nil {
		t.Fatal(err)
	}
	profile.Overrides = append(slices.Clone(profile.Overrides), override)
	if _, err := engine.Reload(profile); err != nil {
		t.Fatalf("Reload onto the node: %v", err)
	}
	udpThroughTUN(t, engine, target)
	if tunnel.datagram.Load() == 0 {
		t.Fatal("the datagrams went around the node")
	}
}
