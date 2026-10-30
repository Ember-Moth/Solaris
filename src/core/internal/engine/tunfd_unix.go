//go:build darwin || linux

package engine

import (
	"fmt"

	LC "github.com/metacubex/mihomo/listener/config"
	"golang.org/x/sys/unix"
)

// hostTUN is the TUN descriptor the host owns in fd mode. sing-tun closes the
// descriptor it runs on whenever mihomo closes or replaces its TUN, so mihomo never gets
// the host's own: it runs on a duplicate the engine makes, and the host's descriptor stays
// open until the host closes it.
type hostTUN struct {
	fd int
	// name is the TUN interface behind fd; a descriptor naming it is a duplicate of fd.
	name string
	// dup is the duplicate mihomo runs on, 0 when there is none.
	dup int
}

func newHostTUN(fd int) (*hostTUN, error) {
	name, err := tunName(fd)
	if err != nil {
		return nil, fmt.Errorf("descriptor %d is not a TUN: %w", fd, err)
	}
	return &hostTUN{fd: fd, name: name}, nil
}

// prepare sets the descriptor mihomo's TUN runs on. With the settings mihomo already runs
// (running is listener.LastTunConf), it is the current duplicate, so that mihomo keeps its
// TUN; otherwise a new one, and mihomo closes the one it replaces.
func (h *hostTUN) prepare(tun *LC.Tun, running LC.Tun) error {
	if h.dup != 0 && running.Enable && running.FileDescriptor == h.dup {
		candidate := *tun
		candidate.FileDescriptor = h.dup
		candidate.Sort()
		if candidate.Equal(running) {
			tun.FileDescriptor = h.dup
			return nil
		}
	}
	// Above the standard streams, and never 0, which mihomo reads as "no descriptor".
	dup, err := unix.FcntlInt(uintptr(h.fd), unix.F_DUPFD_CLOEXEC, 3)
	if err != nil {
		return fmt.Errorf("duplicate the TUN descriptor: %w", err)
	}
	// sing-tun makes a given descriptor non-blocking on macOS but not on Linux. Go closes a
	// blocking file only once its pending Read returns, so a replaced TUN would keep its
	// duplicate, and the TUN, until the next packet arrives.
	if err := unix.SetNonblock(dup, true); err != nil {
		_ = unix.Close(dup)
		return fmt.Errorf("make the TUN descriptor non-blocking: %w", err)
	}
	h.dup = dup
	tun.FileDescriptor = dup
	return nil
}

// settle runs after every change to mihomo's TUN. When mihomo does not run on the
// duplicate, it either closed it with its TUN, or failed to open a TUN on it and left it
// open; the duplicate is closed in the second case only, recognized by still naming the
// TUN, since its number may belong to another file by now.
func (h *hostTUN) settle(running LC.Tun) {
	if h.dup == 0 || (running.Enable && running.FileDescriptor == h.dup) {
		return
	}
	if name, err := tunName(h.dup); err == nil && name == h.name {
		_ = unix.Close(h.dup)
	}
	h.dup = 0
}
