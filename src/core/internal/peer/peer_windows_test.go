//go:build windows

package peer

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
)

// verifySelf connects this test process to a pipe guarded by verify, and returns what
// verify made of it.
func verifySelf(t *testing.T, verify Verifier) (Identity, error) {
	t.Helper()
	name := fmt.Sprintf(`\\.\pipe\solaris-peer-test-%d-%d`, os.Getpid(), time.Now().UnixNano())
	listener, err := winio.ListenPipe(name, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	type result struct {
		identity Identity
		err      error
	}
	results := make(chan result, 1)
	go func() {
		accepted, err := listener.Accept()
		if err != nil {
			results <- result{err: err}
			return
		}
		conn, identity, err := verify(accepted)
		if err != nil {
			accepted.Close()
		} else {
			conn.Close()
		}
		results <- result{identity, err}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := winio.DialPipeContext(ctx, name)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	got := <-results
	return got.identity, got.err
}

func TestPipeClientVerifier(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	var session uint32
	if err := windows.ProcessIdToSessionId(uint32(os.Getpid()), &session); err != nil {
		t.Fatal(err)
	}
	t.Logf("the test runs in session %d", session)
	identity, err := verifySelf(t, PipeClientVerifier(executable, ""))
	switch {
	case session == 0 && (err == nil || !strings.Contains(err.Error(), "session")):
		t.Fatalf("a client in session 0: %+v, %v", identity, err)
	case session != 0 && err != nil:
		t.Fatal(err)
	case session != 0:
		user, err := windows.GetCurrentProcessToken().GetTokenUser()
		if err != nil {
			t.Fatal(err)
		}
		if identity.UserID != user.User.Sid.String() || identity.SessionID != session || identity.ProcessID != uint32(os.Getpid()) {
			t.Fatalf("identity = %+v", identity)
		}
	}

	// A copy at another path, or the same bytes in another file, is not the installed UI.
	copied := filepath.Join(t.TempDir(), "Solaris.exe")
	raw, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(copied, raw, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := verifySelf(t, PipeClientVerifier(copied, "")); err == nil || !strings.Contains(err.Error(), "installed UI") {
		t.Fatalf("a client that is not the installed UI: %v", err)
	}
	if _, err := verifySelf(t, PipeClientVerifier(filepath.Join(t.TempDir(), "missing.exe"), "")); err == nil {
		t.Fatal("accepted a client while the UI is not installed")
	}
	// This test binary carries no signature, so a signed release build refuses it.
	if session != 0 {
		if _, err := verifySelf(t, PipeClientVerifier(executable, "Ember Moth")); err == nil || !strings.Contains(err.Error(), "signature") {
			t.Fatalf("an unsigned client: %v", err)
		}
	}
}

// While a client is connected its executable cannot be changed, renamed or replaced.
func TestVerifiedConnLocksTheExecutable(t *testing.T) {
	executable := filepath.Join(t.TempDir(), "Solaris.exe")
	if err := os.WriteFile(executable, []byte("MZ"), 0o700); err != nil {
		t.Fatal(err)
	}
	image, err := openLocked(executable)
	if err != nil {
		t.Fatal(err)
	}
	held := &heldHandles{}
	held.add(image)
	conn := &verifiedConn{Conn: nopConn{}, held: held}
	if file, err := os.OpenFile(executable, os.O_WRONLY, 0); err == nil {
		file.Close()
		t.Fatal("opened a held executable for writing")
	}
	if err := os.Rename(executable, executable+".old"); err == nil {
		t.Fatal("renamed a held executable")
	}
	if err := conn.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(executable, executable+".old"); err != nil {
		t.Fatalf("the executable is still held after Close: %v", err)
	}
}

func TestTrustedSigner(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	image, err := openLocked(executable)
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(image)
	if name, err := TrustedSigner(executable, image); err == nil {
		t.Fatalf("an unsigned test binary is signed by %q", name)
	}
	// Edge carries its own signature on GitHub's Windows runners.
	edge := filepath.Join(os.Getenv("ProgramFiles(x86)"), `Microsoft\Edge\Application\msedge.exe`)
	signed, err := openLocked(edge)
	if err != nil {
		t.Skipf("no signed program to read: %v", err)
	}
	defer windows.CloseHandle(signed)
	if name, err := TrustedSigner(edge, signed); err != nil || name != "Microsoft Corporation" {
		t.Fatalf("Edge is signed by %q: %v", name, err)
	}
}

type nopConn struct{ net.Conn }

func (nopConn) Close() error { return nil }

var _ io.Closer = (*verifiedConn)(nil)
