//go:build darwin || linux

package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/server"
)

// The development server runs on the developer's own machine, so it must never change
// that machine's network (docs/architecture.md §11). Refusing root means it cannot create
// a TUN, routes or system DNS even if a later code path tried to.
var errRoot = errors.New("the development server must not run as root: it never creates a TUN or changes the network")

func run() error {
	socket := flag.String("socket", "", "development socket path (required)")
	flag.Parse()
	if *socket == "" {
		return errors.New("-socket is required: off Windows the service only runs as a development server")
	}
	if err := refuseRoot(os.Geteuid()); err != nil {
		return err
	}
	uid := uint32(os.Getuid())
	if err := removeStaleSocket(*socket); err != nil {
		return err
	}
	listener, err := net.Listen("unix", *socket)
	if err != nil {
		return err
	}
	if err := os.Chmod(*socket, 0o600); err != nil {
		listener.Close()
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	slog.Info("development control socket", "path", *socket, "uid", uid)
	return server.Serve(ctx, listener, peer.UnixSocketVerifier(uid), newDispatcher(), slog.Default())
}

func refuseRoot(euid int) error {
	if euid == 0 {
		return errRoot
	}
	return nil
}

func removeStaleSocket(path string) error {
	info, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if info.Mode().Type() != fs.ModeSocket {
		return fmt.Errorf("%s exists and is not a socket", path)
	}
	return os.Remove(path)
}
