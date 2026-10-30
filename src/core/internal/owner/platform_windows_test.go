//go:build windows

package owner

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

func currentUser(t *testing.T) *windows.SID {
	t.Helper()
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	return user.User.Sid
}

// openUp lets the test remove what secureDir locked away from it.
func openUp(t *testing.T, dir string) {
	t.Helper()
	t.Cleanup(func() {
		descriptor, err := windows.SecurityDescriptorFromString("D:(A;OICI;FA;;;WD)")
		if err != nil {
			t.Fatal(err)
		}
		dacl, _, _ := descriptor.DACL()
		if err := windows.SetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION, nil, nil, dacl, nil); err != nil {
			t.Errorf("open %s up again: %v", dir, err)
		}
	})
}

func securityOf(t *testing.T, dir string) string {
	t.Helper()
	descriptor, err := windows.GetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT,
		windows.OWNER_SECURITY_INFORMATION|windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatal(err)
	}
	return descriptor.String()
}

func TestSecureDir(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Solaris")
	openUp(t, root)
	if err := secureDir(root, ""); err != nil {
		t.Fatal(err)
	}
	if got := securityOf(t, root); !strings.HasPrefix(got, "O:BAD:P") ||
		!strings.Contains(got, "(A;OICI;FA;;;SY)") || !strings.Contains(got, "(A;OICI;FA;;;BA)") {
		t.Fatalf("root = %s", got)
	}
	// Securing again keeps a directory the service made.
	if err := secureDir(root, ""); err != nil {
		t.Fatal(err)
	}

	user := currentUser(t).String()
	home := filepath.Join(root, user)
	openUp(t, home)
	if err := secureDir(home, user); err != nil {
		t.Fatal(err)
	}
	got := securityOf(t, home)
	if !strings.HasPrefix(got, "O:BAD:P") || !strings.Contains(got, "(A;OICI;FA;;;SY)") ||
		strings.Contains(got, ";BA)") || !strings.Contains(got, ";"+user+")") {
		t.Fatalf("home = %s", got)
	}
	if strings.Contains(got, "(A;OICI;FA;;;"+user+")") {
		t.Fatalf("the owner may write their home: %s", got)
	}
	if err := secureDir(home, "not a SID"); err == nil {
		t.Fatal("secured for a reader that is not a SID")
	}
}

func TestSecureDirRefusesOthersDirectories(t *testing.T) {
	parent := t.TempDir()
	planted := filepath.Join(parent, "planted")
	if err := os.Mkdir(planted, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := windows.SetNamedSecurityInfo(planted, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION,
		currentUser(t), nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := secureDir(planted, ""); err == nil {
		t.Fatal("secured a directory a user made")
	}

	target := filepath.Join(parent, "target")
	if err := os.Mkdir(target, 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(parent, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("no symbolic links here: %v", err)
	}
	if err := secureDir(link, ""); err == nil {
		t.Fatal("secured through a link")
	}
}
