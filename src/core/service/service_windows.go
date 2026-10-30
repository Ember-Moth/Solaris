package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"

	"github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
	"github.com/Ember-Moth/Solaris/src/core/internal/server"
)

const pipeBufferSize = 64 << 10

func run() error {
	console := flag.Bool("console", false, "run in the foreground from an administrator terminal")
	flag.Parse()
	isService, err := svc.IsWindowsService()
	if err != nil {
		return err
	}
	if isService {
		return svc.Run(product.ServiceName, service{})
	}
	if !*console {
		return errors.New("not started by the Service Control Manager; pass -console to run in the foreground")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	return serve(ctx)
}

// serviceHome is the core's home under %ProgramData%. Per-owner homes in users\<SID>\
// arrive with Claim (docs/architecture.md §5.1, §5.4); mihomo opens cache.db once per
// process, so switching owners will need a fresh service process.
func serviceHome() (string, error) {
	programData, err := windows.KnownFolderPath(windows.FOLDERID_ProgramData, 0)
	if err != nil {
		return "", err
	}
	return filepath.Join(programData, product.DisplayName, "core"), nil
}

func serve(ctx context.Context) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	home, err := serviceHome()
	if err != nil {
		return err
	}
	core, err := engine.New(engine.Options{HomeDir: home, Tun: engine.TunOwned})
	if err != nil {
		return err
	}
	defer core.Close()
	dispatcher := control.New(control.Options{Engine: core, HostMethods: true})
	defer dispatcher.Close()
	listener, err := winio.ListenPipe(product.PipeName, &winio.PipeConfig{
		SecurityDescriptor: product.PipeSDDL,
		InputBufferSize:    pipeBufferSize,
		OutputBufferSize:   pipeBufferSize,
	})
	if err != nil {
		return fmt.Errorf("listen on %s: %w", product.PipeName, err)
	}
	verify := peer.PipeClientVerifier(filepath.Join(filepath.Dir(executable), product.UIExecutable))
	return server.Serve(ctx, listener, verify, dispatcher, slog.Default())
}

type service struct{}

func (service) Execute(_ []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- serve(ctx) }()
	status <- svc.Status{
		State:   svc.Running,
		Accepts: svc.AcceptStop | svc.AcceptShutdown | svc.AcceptSessionChange,
	}
	exitCode := func(err error) uint32 {
		if err != nil {
			slog.Error("control server stopped", "error", err)
			return 1
		}
		return 0
	}
	for {
		select {
		case err := <-done:
			status <- svc.Status{State: svc.StopPending}
			return false, exitCode(err)
		case request := <-requests:
			switch request.Cmd {
			case svc.Interrogate:
				status <- request.CurrentStatus
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				cancel()
				return false, exitCode(<-done)
			case svc.SessionChange:
				// The owner signing out stops the tunnel (decision D5, docs/architecture.md §5.4);
				// it comes with Claim in M4.
			}
		}
	}
}
