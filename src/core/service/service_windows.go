package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"

	"github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/owner"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
	"github.com/Ember-Moth/Solaris/src/core/internal/server"
)

const (
	pipeBufferSize = 64 << 10
	logFile        = "service.log"
	// logLimit is when service.log moves aside to service.log.1 at start, replacing it.
	logLimit = 1 << 20
	// geodataDir, beside the executable, holds the GEO data the installer ships.
	geodataDir = "geodata"
	// restartDelay lets the answer to a claim leave before the process stops.
	restartDelay = 200 * time.Millisecond
	// stopWait bounds how long the restart helper waits for the old process to stop.
	stopWait = time.Minute
	// dialWait bounds how long the take-over helper waits for the service's pipe.
	dialWait = 10 * time.Second
)

func run() error {
	console := flag.Bool("console", false, "run in the foreground from an administrator terminal")
	restart := flag.Bool("restart", false, "start the service again once it has stopped; the service runs this for a new owner")
	takeOver := flag.Bool("take-over", false, "stop another user's tunnel and make the user running this the owner; the UI runs this elevated")
	flag.Parse()
	if *restart {
		return restartService()
	}
	if *takeOver {
		return takeOverTunnel()
	}
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
	return serve(ctx, nil, func() {
		slog.Warn("the service has a new owner; start it again to serve them")
		time.AfterFunc(restartDelay, stop)
	})
}

// serviceRoot is %ProgramData%\Solaris, where only SYSTEM and Administrators get in
// (docs/architecture.md §5.1).
func serviceRoot() (string, error) {
	programData, err := windows.KnownFolderPath(windows.FOLDERID_ProgramData, 0)
	if err != nil {
		return "", err
	}
	return filepath.Join(programData, product.DisplayName), nil
}

// sessionEvent is a sign-in or sign-out the SCM reported.
type sessionEvent struct {
	session uint32
	signIn  bool
}

// serve runs the control pipe until ctx ends. restart asks for a new service process,
// which a new owner needs: mihomo's home is fixed for the life of a process (§3.2).
func serve(ctx context.Context, sessions <-chan sessionEvent, restart func()) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	root, err := serviceRoot()
	if err != nil {
		return err
	}
	platform := owner.Windows(restart)
	if err := platform.SecureDir(root, ""); err != nil {
		return err
	}
	logger, closeLog, err := openLog(filepath.Join(root, logFile))
	if err != nil {
		return err
	}
	defer closeLog()
	slog.SetDefault(logger)
	host, home, err := owner.Open(root, filepath.Join(filepath.Dir(executable), geodataDir), platform, logger)
	if err != nil {
		logger.Error("open the owner's home", "error", err)
		return err
	}
	// A service with no owner yet has no engine: it answers GetInfo and Claim only.
	var core control.Engine
	if home != "" {
		opened, err := engine.New(engine.Options{HomeDir: home, Tun: admission.TunOwned})
		if err != nil {
			logger.Error("open the engine", "home", home, "error", err)
			return err
		}
		defer opened.Close()
		core = opened
	}
	dispatcher := control.New(control.Options{Engine: core, HostMethods: true, Host: host})
	defer dispatcher.Close()
	defer host.Close()
	host.Attach(dispatcher)
	go func() {
		for event := range sessions {
			if event.signIn {
				host.SignedIn(event.session)
			} else {
				host.SignedOut(event.session)
			}
		}
	}()

	listener, err := winio.ListenPipe(product.PipeName, &winio.PipeConfig{
		SecurityDescriptor: product.PipeSDDL,
		InputBufferSize:    pipeBufferSize,
		OutputBufferSize:   pipeBufferSize,
	})
	if err != nil {
		return fmt.Errorf("listen on %s: %w", product.PipeName, err)
	}
	signer := ""
	if product.SignedRelease == "true" {
		signer = product.WindowsSigner
	}
	// The service's own executable connects as the take-over helper (decision D5b).
	verify := peer.PipeClientVerifier(filepath.Join(filepath.Dir(executable), product.UIExecutable), executable, signer)
	logger.Info("service started", "version", product.Version, "core", engine.Version(), "owned", home != "")
	return server.Serve(ctx, listener, verify, dispatcher, logger)
}

// openLog appends to service.log, starting it over once it outgrew logLimit.
func openLog(path string) (*slog.Logger, func(), error) {
	if info, err := os.Stat(path); err == nil && info.Size() > logLimit {
		_ = os.Rename(path, path+".1")
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, nil, err
	}
	// A service has no console; the foreground run also prints there.
	logger := slog.New(slog.NewTextHandler(io.MultiWriter(file, os.Stderr), nil))
	return logger, func() { file.Close() }, nil
}

// restartLater starts a helper that starts the service again once this process stopped,
// then stops it.
func restartLater(stop context.CancelFunc) {
	executable, err := os.Executable()
	if err == nil {
		helper := exec.Command(executable, "-restart")
		helper.SysProcAttr = &syscall.SysProcAttr{
			HideWindow:    true,
			CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS,
		}
		err = helper.Start()
	}
	if err != nil {
		slog.Error("start the restart helper; the SCM's recovery restarts the service instead", "error", err)
	}
	time.AfterFunc(restartDelay, stop)
}

// restartService is the helper: it waits for the service to stop, then starts it.
func restartService() error {
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	service, err := manager.OpenService(product.ServiceName)
	if err != nil {
		return err
	}
	defer service.Close()
	for deadline := time.Now().Add(stopWait); ; time.Sleep(100 * time.Millisecond) {
		status, err := service.Query()
		if err != nil {
			return err
		}
		if status.State == svc.Stopped {
			return service.Start()
		}
		if time.Now().After(deadline) {
			return errors.New("the service did not stop")
		}
	}
}

// takeOverTunnel is the take-over helper, which the UI runs elevated once the administrator
// confirmed it through UAC (decision D5b, docs/architecture.md §5.4). The service accepts
// this executable on its pipe only with an administrator's rights.
func takeOverTunnel() error {
	ctx, cancel := context.WithTimeout(context.Background(), dialWait)
	defer cancel()
	conn, err := winio.DialPipeContext(ctx, product.PipeName)
	if err != nil {
		return err
	}
	defer conn.Close()
	return requestTakeOver(conn)
}

type service struct{}

func (service) Execute(_ []string, requests <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sessions := make(chan sessionEvent, 64)
	done := make(chan error, 1)
	// A new owner restarts the service, and only an error exit makes the SCM's recovery
	// restart it; the helper does it at once.
	go func() { done <- serve(ctx, sessions, func() { restartLater(cancel) }) }()
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
				// The owner signing out stops the tunnel and their signing in brings it back
				// (decision D5, docs/architecture.md §5.4); serve takes it from here.
				notification := *(**windows.WTSSESSION_NOTIFICATION)(unsafe.Pointer(&request.EventData))
				var event sessionEvent
				switch request.EventType {
				case windows.WTS_SESSION_LOGON:
					event = sessionEvent{session: notification.SessionID, signIn: true}
				case windows.WTS_SESSION_LOGOFF:
					event = sessionEvent{session: notification.SessionID}
				default:
					continue
				}
				select {
				case sessions <- event:
				default:
					slog.Warn("dropped a session change", "session", event.session)
				}
			}
		}
	}
}
