//go:build windows

package owner

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"

	"github.com/Ember-Moth/Solaris/src/core/internal/power"
	"github.com/Ember-Moth/Solaris/src/core/internal/sysproxy"
)

// Administrators own what the service creates, so they can always get it back; SYSTEM alone
// writes it. The owner may read their home but not write it: mihomo runs as SYSTEM there, and
// a link the owner put in it would let them steer its writes anywhere (§5.1, §8).
const (
	rootSDDL = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
	homeSDDL = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FR;;;%s)"
)

// Windows is the service's Platform; restart replaces the service process.
func Windows(restart func()) Platform { return windowsPlatform{restart: restart} }

type windowsPlatform struct{ restart func() }

func (p windowsPlatform) Restart() { p.restart() }

func (windowsPlatform) NetworkReady() bool { return power.NetworkAvailable() }

func (windowsPlatform) ReadProxy(session uint32) (sysproxy.Settings, error) {
	return sysproxy.Read(session)
}

func (windowsPlatform) WriteProxy(session uint32, settings sysproxy.Settings) error {
	return sysproxy.Write(session, settings)
}

func (windowsPlatform) SecureDir(dir, reader string) error { return secureDir(dir, reader) }

func secureDir(dir, reader string) error {
	sddl := rootSDDL
	if reader != "" {
		if !ValidSID(reader) {
			return fmt.Errorf("%q is not a SID", reader)
		}
		sddl = fmt.Sprintf(homeSDDL, reader)
	}
	created := true
	if err := os.Mkdir(dir, 0o700); errors.Is(err, fs.ErrExist) {
		created = false
	} else if err != nil {
		return err
	}
	if err := checkDir(dir, created); err != nil {
		return err
	}
	descriptor, err := windows.SecurityDescriptorFromString(sddl)
	if err != nil {
		return err
	}
	owner, _, err := descriptor.Owner()
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	information := windows.SECURITY_INFORMATION(windows.OWNER_SECURITY_INFORMATION |
		windows.DACL_SECURITY_INFORMATION | windows.PROTECTED_DACL_SECURITY_INFORMATION)
	if err := windows.SetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT, information, owner, nil, dacl, nil); err != nil {
		return fmt.Errorf("secure %s: %w", dir, err)
	}
	return nil
}

// checkDir refuses a link in place of the directory, and a directory that someone other than
// SYSTEM or Administrators made: its owner could change its ACL again, or have left links
// inside for the service to follow.
func checkDir(dir string, created bool) error {
	path, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return err
	}
	attributes, err := windows.GetFileAttributes(path)
	if err != nil {
		return fmt.Errorf("inspect %s: %w", dir, err)
	}
	if attributes&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 || attributes&windows.FILE_ATTRIBUTE_DIRECTORY == 0 {
		return fmt.Errorf("%s is not a plain directory", dir)
	}
	if created {
		return nil
	}
	descriptor, err := windows.GetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION)
	if err != nil {
		return fmt.Errorf("read the owner of %s: %w", dir, err)
	}
	owner, _, err := descriptor.Owner()
	if err != nil {
		return err
	}
	if !owner.IsWellKnown(windows.WinLocalSystemSid) && !owner.IsWellKnown(windows.WinBuiltinAdministratorsSid) {
		return fmt.Errorf("%s belongs to %s, not to SYSTEM or Administrators", dir, owner)
	}
	return nil
}

func (windowsPlatform) SessionUser(session uint32) (string, error) {
	var token windows.Token
	if err := windows.WTSQueryUserToken(session, &token); err != nil {
		return "", err
	}
	defer token.Close()
	user, err := token.GetTokenUser()
	if err != nil {
		return "", err
	}
	return user.User.Sid.String(), nil
}

func (p windowsPlatform) SignedIn(sid string) []uint32 {
	var sessions *windows.WTS_SESSION_INFO
	var count uint32
	if err := windows.WTSEnumerateSessions(0, 0, 1, &sessions, &count); err != nil {
		return nil
	}
	defer windows.WTSFreeMemory(uintptr(unsafe.Pointer(sessions)))
	var found []uint32
	for _, session := range unsafe.Slice(sessions, count) {
		// A disconnected session is still signed in, and still runs its user's programs.
		if session.State != windows.WTSActive && session.State != windows.WTSDisconnected {
			continue
		}
		if user, err := p.SessionUser(session.SessionID); err == nil && user == sid {
			found = append(found, session.SessionID)
		}
	}
	return found
}
