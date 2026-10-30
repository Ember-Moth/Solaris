package geodata

import (
	"os"
	"path/filepath"
	"testing"
)

func writeFiles(t *testing.T, dir string, files map[string]string) {
	t.Helper()
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func readDir(t *testing.T, dir string) map[string]string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string]string{}
	for _, entry := range entries {
		content, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		files[entry.Name()] = string(content)
	}
	return files
}

func TestSeedCopiesOnlyWhatTheHomeLacks(t *testing.T) {
	bundled := t.TempDir()
	writeFiles(t, bundled, map[string]string{"GeoSite.dat": "bundled site", "GeoIP.dat": "bundled ip"})
	home := filepath.Join(t.TempDir(), "Solaris", "core")
	if err := os.MkdirAll(home, 0o700); err != nil {
		t.Fatal(err)
	}
	writeFiles(t, home, map[string]string{"GeoIP.dat": "updated ip"})
	if err := Seed(bundled, home); err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"GeoSite.dat": "bundled site", "GeoIP.dat": "updated ip"}
	if got := readDir(t, home); len(got) != len(want) || got["GeoSite.dat"] != want["GeoSite.dat"] || got["GeoIP.dat"] != want["GeoIP.dat"] {
		t.Fatalf("home = %v, want %v", got, want)
	}
}

func TestSeedCreatesTheHome(t *testing.T) {
	bundled := t.TempDir()
	writeFiles(t, bundled, map[string]string{"GeoSite.dat": "site", "GeoIP.dat": "ip"})
	home := filepath.Join(t.TempDir(), "Solaris", "core")
	if err := Seed(bundled, home); err != nil {
		t.Fatal(err)
	}
	if got := readDir(t, home); len(got) != 2 || got["GeoSite.dat"] != "site" || got["GeoIP.dat"] != "ip" {
		t.Fatalf("home = %v", got)
	}
}

func TestSeedLeavesNoPartialFile(t *testing.T) {
	bundled := t.TempDir()
	writeFiles(t, bundled, map[string]string{"GeoSite.dat": "site"})
	home := t.TempDir()
	if err := Seed(bundled, home); err == nil {
		t.Fatal("seeded without the bundled GeoIP.dat")
	}
	if got := readDir(t, home); len(got) != 1 || got["GeoSite.dat"] != "site" {
		t.Fatalf("home = %v, want GeoSite.dat alone", got)
	}
}
