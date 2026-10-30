//go:build darwin || linux

package engine

import (
	"net/netip"
	"testing"

	C "github.com/metacubex/mihomo/constant"
	LC "github.com/metacubex/mihomo/listener/config"
	"golang.org/x/sys/unix"
)

// These tests stand pipes in for the TUN: none of them opens a TUN or touches the
// network. The descriptor handling on a real TUN runs in CI (tun_fd_linux_test.go).

func pipe(t *testing.T) int {
	t.Helper()
	var fds [2]int
	if err := unix.Pipe(fds[:]); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = unix.Close(fds[0])
		_ = unix.Close(fds[1])
	})
	return fds[0]
}

func isOpen(fd int) bool {
	_, err := unix.FcntlInt(uintptr(fd), unix.F_GETFD, 0)
	return err == nil
}

func fdTun(mtu uint32) LC.Tun {
	return LC.Tun{
		Enable:       true,
		Stack:        C.TunMips,
		Inet4Address: []netip.Prefix{FDNetwork.Inet4},
		MTU:          mtu,
	}
}

func TestHostTUNRefusesADescriptorThatIsNotATUN(t *testing.T) {
	if _, err := newHostTUN(pipe(t)); err == nil {
		t.Fatal("a pipe passed for the TUN")
	}
	if _, err := New(Options{HomeDir: testHome, Tun: TunFD, TunFD: pipe(t)}); err == nil {
		t.Fatal("the engine opened in fd mode on a pipe")
	}
}

func TestHostTUNKeepsTheDuplicateWhileTheSettingsHold(t *testing.T) {
	host := &hostTUN{fd: pipe(t), name: "utun-test"}
	t.Cleanup(func() { _ = unix.Close(host.dup) })

	first := fdTun(FDNetwork.MTU)
	if err := host.prepare(&first, LC.Tun{}); err != nil {
		t.Fatal(err)
	}
	if first.FileDescriptor <= 2 || first.FileDescriptor == host.fd || !isOpen(first.FileDescriptor) {
		t.Fatalf("mihomo got descriptor %d (host %d)", first.FileDescriptor, host.fd)
	}
	// mihomo runs it, as ReCreateTun records it.
	running := first
	running.Sort()

	same := fdTun(FDNetwork.MTU)
	if err := host.prepare(&same, running); err != nil {
		t.Fatal(err)
	}
	if same.FileDescriptor != first.FileDescriptor {
		t.Fatalf("unchanged settings got descriptor %d, want %d", same.FileDescriptor, first.FileDescriptor)
	}

	changed := fdTun(1400)
	if err := host.prepare(&changed, running); err != nil {
		t.Fatal(err)
	}
	if changed.FileDescriptor == first.FileDescriptor || host.dup != changed.FileDescriptor {
		t.Fatalf("changed settings kept descriptor %d", changed.FileDescriptor)
	}
	// mihomo closes the duplicate it replaces.
	_ = unix.Close(first.FileDescriptor)
}

func TestHostTUNSettleLeavesOtherFilesAlone(t *testing.T) {
	host := &hostTUN{fd: pipe(t), name: "utun-test"}
	tun := fdTun(FDNetwork.MTU)
	if err := host.prepare(&tun, LC.Tun{}); err != nil {
		t.Fatal(err)
	}
	dup := host.dup

	host.settle(tun)
	if host.dup != dup || !isOpen(dup) {
		t.Fatal("settle let go of the duplicate mihomo runs on")
	}

	// mihomo closed the duplicate with its TUN, and the number now belongs to another file.
	if err := unix.Dup2(pipe(t), dup); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = unix.Close(dup) })
	host.settle(LC.Tun{})
	if host.dup != 0 || !isOpen(dup) {
		t.Fatalf("settle closed descriptor %d, which is not the TUN", dup)
	}
}
