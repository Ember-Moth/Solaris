package geodata

import (
	"maps"
	"os"
	"path/filepath"
	"testing"
)

func writeBundledFiles(t *testing.T, dir string) map[string]string {
	t.Helper()
	files := map[string]string{
		"GeoSite.dat":  "bundled site",
		"GeoIP.dat":    "bundled ip",
		"geoip.metadb": "bundled mmdb",
		"ASN.mmdb":     "bundled asn",
	}
	writeFiles(t, dir, files)
	return files
}

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
	files := writeBundledFiles(t, bundled)
	for name := range files {
		t.Run(name, func(t *testing.T) {
			home := t.TempDir()
			writeFiles(t, home, map[string]string{name: "updated data"})
			if err := Seed(bundled, home); err != nil {
				t.Fatal(err)
			}
			want := maps.Clone(files)
			want[name] = "updated data"
			if got := readDir(t, home); !maps.Equal(got, want) {
				t.Fatalf("home = %v, want %v", got, want)
			}
		})
	}
}

func TestSeedCreatesTheHome(t *testing.T) {
	bundled := t.TempDir()
	want := writeBundledFiles(t, bundled)
	home := filepath.Join(t.TempDir(), "Solaris", "core")
	if err := Seed(bundled, home); err != nil {
		t.Fatal(err)
	}
	if got := readDir(t, home); !maps.Equal(got, want) {
		t.Fatalf("home = %v", got)
	}
}

func TestSeedAddsMMDBToAnExistingDATHome(t *testing.T) {
	bundled := t.TempDir()
	want := writeBundledFiles(t, bundled)
	home := t.TempDir()
	previous := map[string]string{"GeoSite.dat": "updated site", "GeoIP.dat": "updated ip"}
	writeFiles(t, home, previous)
	maps.Copy(want, previous)
	if err := Seed(bundled, home); err != nil {
		t.Fatal(err)
	}
	if got := readDir(t, home); !maps.Equal(got, want) {
		t.Fatalf("home = %v, want %v", got, want)
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
