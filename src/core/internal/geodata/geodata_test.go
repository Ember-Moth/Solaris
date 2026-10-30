package geodata

import (
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/geodata/geodatatest"
)

func TestCategories(t *testing.T) {
	dir := t.TempDir()
	if err := geodatatest.Write(dir); err != nil {
		t.Fatal(err)
	}
	sites, err := Categories(filepath.Join(dir, "GeoSite.dat"))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(sites.GetNames(), []string{"cn", "openai", "private", "telegram"}) || len(sites.GetSha256()) != 64 {
		t.Fatalf("geosite = %v", sites)
	}
	ips, err := Categories(filepath.Join(dir, "GeoIP.dat"))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(ips.GetNames(), []string{"cn", "private", "telegram"}) {
		t.Fatalf("geoip = %v", ips)
	}
}

func TestCategoriesOfMissingAndBrokenFiles(t *testing.T) {
	dir := t.TempDir()
	missing, err := Categories(filepath.Join(dir, "GeoSite.dat"))
	if err != nil || len(missing.GetNames()) != 0 || missing.GetSha256() != "" {
		t.Fatalf("missing file: %v, %v", missing, err)
	}
	broken := filepath.Join(dir, "GeoIP.dat")
	if err := os.WriteFile(broken, []byte{0x0a, 0xff}, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Categories(broken); err == nil {
		t.Fatal("a truncated file read fine")
	}
}

// Upper-case codes, as v2fly's lists write them, read as rules write them.
func TestCategoryNamesLowerCase(t *testing.T) {
	// GeoSiteList{entry: [GeoSite{country_code: "GOOGLE"}, GeoSite{country_code: "CN"}]}
	data := []byte{0x0a, 0x08, 0x0a, 0x06, 'G', 'O', 'O', 'G', 'L', 'E', 0x0a, 0x04, 0x0a, 0x02, 'C', 'N'}
	names, err := categoryNames(data)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(names, []string{"cn", "google"}) {
		t.Fatalf("names = %v", names)
	}
}
