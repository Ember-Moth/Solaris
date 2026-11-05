//go:build windows

package owner

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A reader of state.json holds it open while a Stop writes was_running. The write waits for
// the reader instead of failing, which would leave was_running true.
func TestWriteFileWaitsForAReaderToLetGo(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, stateFile)
	if err := writeFile(path, []byte("old")); err != nil {
		t.Fatal(err)
	}
	reader, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	other := filepath.Join(dir, "other")
	if err := os.WriteFile(other, []byte("other"), 0o600); err != nil {
		t.Fatal(err)
	}
	if os.Rename(other, path) == nil {
		t.Skip("Windows replaced a file a reader holds open")
	}

	written := make(chan error, 1)
	go func() { written <- writeFile(path, []byte("new")) }()
	time.Sleep(5 * replaceRetry)
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	if err := <-written; err != nil {
		t.Fatal(err)
	}
	if raw, err := os.ReadFile(path); err != nil || string(raw) != "new" {
		t.Fatalf("state.json = %q, %v", raw, err)
	}
}
