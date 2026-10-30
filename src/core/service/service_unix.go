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
	"path/filepath"
	"syscall"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
	"github.com/Ember-Moth/Solaris/src/core/internal/server"
)

// The development server runs on the developer's own machine, so it must never change
// that machine's network (docs/architecture.md §11). Refusing root means it cannot create
// a TUN, routes or system DNS even if a later code path tried to.
var errRoot = errors.New("the development server must not run as root: it never creates a TUN or changes the network")

func run() error {
	socket := flag.String("socket", "", "development socket path (required)")
	home := flag.String("home", "", "mihomo home directory (default: the user config directory)")
	flag.Parse()
	if *socket == "" {
		return errors.New("-socket is required: off Windows the service only runs as a development server")
	}
	if err := refuseRoot(os.Geteuid()); err != nil {
		return err
	}
	if *home == "" {
		var err error
		if *home, err = developmentHome(); err != nil {
			return err
		}
	}
	core, err := newDevelopmentEngine(*home)
	if err != nil {
		return err
	}
	defer core.Close()
	dispatcher := newDevelopmentDispatcher(core)
	defer dispatcher.Close()

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
	slog.Info("development control socket", "path", *socket, "uid", uid, "home", core.HomeDir())
	return server.Serve(ctx, listener, peer.UnixSocketVerifier(uid), dispatcher, slog.Default())
}

// developmentHome keeps the development core's files apart from anything a release writes.
func developmentHome() (string, error) {
	config, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(config, product.DisplayName, "dev-core"), nil
}

// newDevelopmentEngine runs mihomo without a TUN: whatever the profile says, nothing
// takes over the machine's traffic and every inbound stays on 127.0.0.1.
func newDevelopmentEngine(home string) (*engine.Engine, error) {
	return engine.New(engine.Options{HomeDir: home, Tun: engine.TunNone})
}

// newDevelopmentDispatcher serves the host methods so ServiceHost works against it, and
// rejects the TUN and the system proxy with unsupported_method.
func newDevelopmentDispatcher(core *engine.Engine) *control.Dispatcher {
	return control.New(control.Options{Engine: core, HostMethods: true, Development: true})
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
