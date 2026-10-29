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
	"strconv"
	"syscall"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/server"
)

func run() error {
	socket := flag.String("socket", "", "development socket path (required)")
	flag.Parse()
	if *socket == "" {
		return errors.New("-socket is required: off Windows the service only runs as a development server")
	}
	allowedUID, err := developerUID()
	if err != nil {
		return err
	}
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
	if os.Getuid() == 0 && allowedUID != 0 {
		if err := os.Chown(*socket, int(allowedUID), -1); err != nil {
			listener.Close()
			return err
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	slog.Info("development control socket", "path", *socket, "uid", allowedUID)
	return server.Serve(ctx, listener, peer.UnixSocketVerifier(allowedUID), newDispatcher(), slog.Default())
}

// developerUID is the user allowed to connect; under sudo that is the invoking user.
func developerUID() (uint32, error) {
	value := os.Getenv("SUDO_UID")
	if value == "" || os.Getuid() != 0 {
		return uint32(os.Getuid()), nil
	}
	uid, err := strconv.ParseUint(value, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("invalid SUDO_UID %q", value)
	}
	return uint32(uid), nil
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
