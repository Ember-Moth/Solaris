//go:build windows

package geodata

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

// The copies take the home's ACL. The service's homes let their owner read and SYSTEM alone
// write (docs/architecture.md §5.1); a copy moved in from elsewhere would keep the ACL of
// where it was made.
func TestSeedTakesTheHomesACL(t *testing.T) {
	bundled := t.TempDir()
	writeFiles(t, bundled, map[string]string{"GeoSite.dat": "site", "GeoIP.dat": "ip"})
	home := filepath.Join(t.TempDir(), "home")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	// Users may read what the home holds: an entry that no temporary directory has.
	descriptor, err := windows.SecurityDescriptorFromString(fmt.Sprintf("D:P(A;OICI;FA;;;%s)(A;OICI;FR;;;BU)", user.User.Sid))
	if err != nil {
		t.Fatal(err)
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		t.Fatal(err)
	}
	if err := windows.SetNamedSecurityInfo(home, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil); err != nil {
		t.Fatal(err)
	}
	if err := Seed(bundled, home); err != nil {
		t.Fatal(err)
	}
	for _, name := range Files {
		file, err := windows.GetNamedSecurityInfo(filepath.Join(home, name), windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
		if err != nil {
			t.Fatal(err)
		}
		if got := file.String(); !strings.Contains(got, "(A;ID;FR;;;BU)") {
			t.Errorf("%s = %s, not the home's ACL", name, got)
		}
	}
}
