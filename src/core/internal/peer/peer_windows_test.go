//go:build windows

package peer

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
	"unsafe"

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
	identity, err := verifySelf(t, PipeClientVerifier(executable, "", ""))
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
		if identity.UserID != user.User.Sid.String() || identity.SessionID != session || identity.ProcessID != uint32(os.Getpid()) ||
			identity.Administrator != elevated(t) {
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
	if _, err := verifySelf(t, PipeClientVerifier(copied, "", "")); err == nil || !strings.Contains(err.Error(), "installed UI") {
		t.Fatalf("a client that is not the installed UI: %v", err)
	}
	if _, err := verifySelf(t, PipeClientVerifier(filepath.Join(t.TempDir(), "missing.exe"), "", "")); err == nil {
		t.Fatal("accepted a client while the UI is not installed")
	}
	// This test binary carries no signature, so a signed release build refuses it.
	if session != 0 {
		if _, err := verifySelf(t, PipeClientVerifier(executable, "", "Ember Moth")); err == nil || !strings.Contains(err.Error(), "signature") {
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

// elevated is whether this test runs with an administrator's rights, as Windows itself
// answers it for the thread's token.
func elevated(t *testing.T) bool {
	t.Helper()
	admins, err := windows.CreateWellKnownSid(windows.WinBuiltinAdministratorsSid)
	if err != nil {
		t.Fatal(err)
	}
	member, err := windows.Token(0).IsMember(admins)
	if err != nil {
		t.Fatal(err)
	}
	return member
}

var createRestrictedToken = windows.NewLazySystemDLL("advapi32.dll").NewProc("CreateRestrictedToken")

// filtered is this process's token with Administrators only denying access, as UAC leaves
// an administrator's token until they confirm an elevation.
func filtered(t *testing.T) windows.Token {
	t.Helper()
	var own windows.Token
	if err := windows.OpenProcessToken(windows.CurrentProcess(), windows.TOKEN_ALL_ACCESS, &own); err != nil {
		t.Fatal(err)
	}
	defer own.Close()
	admins, err := windows.CreateWellKnownSid(windows.WinBuiltinAdministratorsSid)
	if err != nil {
		t.Fatal(err)
	}
	disable := windows.SIDAndAttributes{Sid: admins}
	var token windows.Token
	if ok, _, err := createRestrictedToken.Call(uintptr(own), 0, 1, uintptr(unsafe.Pointer(&disable)),
		0, 0, 0, 0, uintptr(unsafe.Pointer(&token))); ok == 0 {
		t.Fatalf("restrict the token: %v", err)
	}
	t.Cleanup(func() { token.Close() })
	return token
}

// The Administrators group counts only while it is enabled (decision D5b).
func TestAdministrator(t *testing.T) {
	own, err := windows.OpenCurrentProcessToken()
	if err != nil {
		t.Fatal(err)
	}
	defer own.Close()
	admin, err := administrator(own)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("the test runs as an administrator: %v", admin)
	if admin != elevated(t) {
		t.Fatalf("administrator = %v, Windows says %v", admin, !admin)
	}
	if admin, err := administrator(filtered(t)); err != nil || admin {
		t.Fatalf("a filtered token: %v, %v", admin, err)
	}
}

// The service's own executable connects only with an administrator's rights: elevated, as
// the take-over helper runs, it gets through; with the token UAC filters, it does not.
func TestPipeClientVerifierHelper(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ui := filepath.Join(t.TempDir(), "Solaris.exe")
	if err := os.WriteFile(ui, []byte("MZ"), 0o700); err != nil {
		t.Fatal(err)
	}
	verify := PipeClientVerifier(ui, executable, "")
	var session uint32
	if err := windows.ProcessIdToSessionId(uint32(os.Getpid()), &session); err != nil {
		t.Fatal(err)
	}
	identity, err := verifySelf(t, verify)
	switch {
	case session == 0:
		if err == nil || !strings.Contains(err.Error(), "session") {
			t.Fatalf("a helper in session 0: %+v, %v", identity, err)
		}
	case elevated(t):
		if err != nil || !identity.Administrator {
			t.Fatalf("an elevated helper: %+v, %v", identity, err)
		}
	default:
		if err == nil || !strings.Contains(err.Error(), "administrator") {
			t.Fatalf("a helper without an administrator's rights: %+v, %v", identity, err)
		}
	}
	if session == 0 || !elevated(t) {
		return
	}
	if _, err := verifyChild(t, verify, filtered(t)); err == nil || !strings.Contains(err.Error(), "administrator") {
		t.Fatalf("a helper with a filtered token: %v", err)
	}
}

const childPipeEnv = "SOLARIS_PEER_TEST_PIPE"

// TestChildDialsThePipe is the client verifyChild starts: it holds the pipe open until the
// test is done with it.
func TestChildDialsThePipe(t *testing.T) {
	name := os.Getenv(childPipeEnv)
	if name == "" {
		t.Skip("only runs as verifyChild's client")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	conn, err := winio.DialPipeContext(ctx, name)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetReadDeadline(time.Now().Add(30 * time.Second))
	io.Copy(io.Discard, conn)
}

// verifyChild runs this test binary as a client with token, and returns what verify made of
// it.
func verifyChild(t *testing.T, verify Verifier, token windows.Token) (Identity, error) {
	t.Helper()
	name := fmt.Sprintf(`\\.\pipe\solaris-peer-test-%d-%d`, os.Getpid(), time.Now().UnixNano())
	// Everyone may connect: the filtered token gets nothing from Administrators.
	listener, err := winio.ListenPipe(name, &winio.PipeConfig{SecurityDescriptor: "D:P(A;;GA;;;WD)"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	child := exec.Command(executable, "-test.run=^TestChildDialsThePipe$")
	child.Env = append(os.Environ(), childPipeEnv+"="+name)
	child.SysProcAttr = &syscall.SysProcAttr{Token: syscall.Token(token)}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		child.Process.Kill()
		child.Wait()
	}()
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
	select {
	case got := <-results:
		return got.identity, got.err
	case <-time.After(30 * time.Second):
		t.Fatal("the child never connected")
	}
	return Identity{}, nil
}

type nopConn struct{ net.Conn }

func (nopConn) Close() error { return nil }

var _ io.Closer = (*verifiedConn)(nil)
