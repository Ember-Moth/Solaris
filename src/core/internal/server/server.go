// Package server runs the framed control protocol over a stream listener: the Windows
// service pipe, or the development socket elsewhere (docs/architecture.md §4.2).
package server

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"sync"
	"time"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/frame"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
)

type Handler interface {
	Handle(ctx context.Context, caller peer.Identity, payload []byte) []byte
}

// maxInFlight bounds concurrent requests on one connection; reading pauses until a slot frees.
const maxInFlight = 16

const acceptRetryDelay = 100 * time.Millisecond

// Serve accepts connections until ctx is cancelled, then waits for open connections to finish.
func Serve(ctx context.Context, listener net.Listener, verify peer.Verifier, handler Handler, logger *slog.Logger) error {
	stopAccepting := context.AfterFunc(ctx, func() { listener.Close() })
	defer stopAccepting()
	var connections sync.WaitGroup
	defer connections.Wait()
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			if errors.Is(err, net.ErrClosed) {
				return err
			}
			logger.Warn("accept control connection", "error", err)
			time.Sleep(acceptRetryDelay)
			continue
		}
		connections.Go(func() { serveConn(ctx, conn, verify, handler, logger) })
	}
}

func serveConn(ctx context.Context, conn net.Conn, verify peer.Verifier, handler Handler, logger *slog.Logger) {
	defer conn.Close()
	caller, err := verify(conn)
	if err != nil {
		logger.Warn("rejected control client", "error", err)
		return
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	context.AfterFunc(ctx, func() { conn.Close() })

	var writes sync.Mutex
	var requests sync.WaitGroup
	defer requests.Wait()
	slots := make(chan struct{}, maxInFlight)
	for {
		payload, err := frame.Read(conn, frame.KindRequest)
		if err != nil {
			if !errors.Is(err, io.EOF) && ctx.Err() == nil {
				logger.Info("control connection closed", "user", caller.UserID, "error", err)
			}
			return
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			return
		}
		requests.Go(func() {
			defer func() { <-slots }()
			response := handler.Handle(ctx, caller, payload)
			writes.Lock()
			defer writes.Unlock()
			if err := frame.Write(conn, frame.KindResponse, response); err != nil {
				cancel()
			}
		})
	}
}
