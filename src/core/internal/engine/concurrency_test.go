package engine

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// Keep existing streams active and keep accepting new ones while applying profiles.
// This exercises the finalizer/log, inbound policy and credential-store boundaries
// together, without changing the host network or relying on race-only waits.
func TestReloadWhileAcceptingAndTransferring(t *testing.T) {
	e := openEngine(t, admission.TunNone)
	echo, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer echo.Close()
	go func() {
		for {
			conn, err := echo.Accept()
			if err != nil {
				return
			}
			go func() { defer conn.Close(); _, _ = io.Copy(conn, conn) }()
		}
	}()
	port := porttest.Free(t)
	profile := func(i int) admission.Profile {
		level := "warning"
		if i%2 == 0 {
			level = "error"
		}
		source := fmt.Sprintf("mixed-port: %d\nlog-level: %s\nauthentication: [u:p, v:q%d]\nlan-allowed-ips: [127.0.0.0/8]\nlan-disallowed-ips: []\nskip-auth-prefixes: []\nrules: [\"MATCH,DIRECT\"]\n", port, level, i)
		return admission.Profile{Config: []byte(source)}
	}
	if _, err := e.Start(profile(0)); err != nil {
		t.Fatal(err)
	}
	defer e.Stop()
	connect := func() (net.Conn, *bufio.Reader, error) {
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), time.Second)
		if err != nil {
			return nil, nil, err
		}
		_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
		_, err = fmt.Fprintf(conn, "CONNECT %s HTTP/1.1\r\nHost: %s\r\nProxy-Authorization: Basic dTpw\r\n\r\n", echo.Addr(), echo.Addr())
		reader := bufio.NewReader(conn)
		if err == nil {
			var response *http.Response
			response, err = http.ReadResponse(reader, &http.Request{Method: http.MethodConnect})
			if err == nil && response.StatusCode != http.StatusOK {
				err = fmt.Errorf("CONNECT: %s", response.Status)
			}
		}
		if err != nil {
			conn.Close()
			return nil, nil, err
		}
		return conn, reader, nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	defer func() { cancel(); wg.Wait() }()
	var progress [4]atomic.Uint64
	ready := make(chan struct{}, len(progress))
	for i := range progress {
		conn, reader, err := connect()
		if err != nil {
			t.Fatal(err)
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer conn.Close()
			payload := []byte(strings.Repeat(fmt.Sprint(i), 4096))
			got := make([]byte, len(payload))
			first := true
			for ctx.Err() == nil {
				if _, err := conn.Write(payload); err != nil {
					t.Error(err)
					return
				}
				if _, err := io.ReadFull(reader, got); err != nil {
					t.Error(err)
					return
				}
				if !bytes.Equal(payload, got) {
					t.Error("stream corruption")
					return
				}
				progress[i].Add(uint64(len(got)))
				if first {
					ready <- struct{}{}
					first = false
				}
			}
		}()
	}
	for range progress {
		select {
		case <-ready:
		case <-time.After(10 * time.Second):
			t.Fatal("stream never made progress")
		}
	}
	var accepts atomic.Uint64
	wg.Add(1)
	go func() {
		defer wg.Done()
		for ctx.Err() == nil {
			conn, _, err := connect()
			if err != nil {
				t.Error(err)
				return
			}
			accepts.Add(1)
			conn.Close()
		}
	}()
	var before [4]uint64
	for i := range progress {
		before[i] = progress[i].Load()
	}
	for i := range 40 {
		if _, err := e.Reload(profile(i)); err != nil {
			t.Fatal(err)
		}
		if i%5 == 0 {
			runtime.GC()
		}
	}
	cancel()
	wg.Wait()
	for i := range progress {
		if progress[i].Load() <= before[i] {
			t.Errorf("stream %d stopped during reload", i)
		}
	}
	if accepts.Load() == 0 {
		t.Error("no connections accepted during reload")
	}
}
