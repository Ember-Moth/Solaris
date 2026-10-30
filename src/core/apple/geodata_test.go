//go:build cgo

package main

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

func TestAttachSeedsTheHomeBeforeOpeningTheEngine(t *testing.T) {
	resources := t.TempDir()
	writeFiles(t, resources, map[string]string{"GeoSite.dat": "site", "GeoIP.dat": "ip"})
	home := t.TempDir()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	if err := attach(int(reader.Fd()), home, resources, false); err == nil {
		detach()
		t.Fatal("attached a pipe as the TUN")
	}
	if got := readDir(t, home); got["GeoSite.dat"] != "site" || got["GeoIP.dat"] != "ip" {
		t.Fatalf("home = %v", got)
	}
}
