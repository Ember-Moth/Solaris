package server

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"sort"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

type echo struct{}

func (echo) Handle(_ context.Context, caller peer.Identity, payload []byte) []byte {
	return append([]byte(caller.UserID+":"), payload...)
}

func start(t *testing.T, verify peer.Verifier) (string, context.CancelFunc, <-chan error) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	go func() { done <- Serve(ctx, listener, verify, echo{}, logger) }()
	return listener.Addr().String(), cancel, done
}

func dial(t *testing.T, address string) net.Conn {
	t.Helper()
	conn, err := net.Dial("tcp", address)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	conn.SetDeadline(time.Now().Add(5 * time.Second))
	return conn
}

func TestServesConcurrentRequestsOnOneConnection(t *testing.T) {
	address, cancel, done := start(t, func(net.Conn) (peer.Identity, error) {
		return peer.Identity{UserID: "user"}, nil
	})
	conn := dial(t, address)
	for _, payload := range []string{"a", "b", "c"} {
		if err := frame.Write(conn, frame.KindRequest, []byte(payload)); err != nil {
			t.Fatal(err)
		}
	}
	var got []string
	for range 3 {
		response, err := frame.Read(conn, frame.KindResponse)
		if err != nil {
			t.Fatal(err)
		}
		got = append(got, string(response))
	}
	sort.Strings(got)
	if want := []string{"user:a", "user:b", "user:c"}; !equal(got, want) {
		t.Fatalf("responses = %v, want %v", got, want)
	}

	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Serve returned %v after cancellation", err)
	}
}

func TestRejectedClientIsDisconnected(t *testing.T) {
	address, cancel, done := start(t, func(net.Conn) (peer.Identity, error) {
		return peer.Identity{}, errors.New("not the UI")
	})
	defer func() { cancel(); <-done }()
	conn := dial(t, address)
	if _, err := conn.Read(make([]byte, 1)); !errors.Is(err, io.EOF) {
		t.Fatalf("read after rejection = %v, want EOF", err)
	}
}

func TestBadHeaderClosesConnection(t *testing.T) {
	address, cancel, done := start(t, func(net.Conn) (peer.Identity, error) {
		return peer.Identity{UserID: "user"}, nil
	})
	defer func() { cancel(); <-done }()
	conn := dial(t, address)
	if _, err := conn.Write(bytes.Repeat([]byte{'x'}, frame.HeaderSize)); err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Read(make([]byte, 1)); !errors.Is(err, io.EOF) {
		t.Fatalf("read after bad header = %v, want EOF", err)
	}
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
