//go:build windows

package sysproxy

import (
	"os"
	"testing"
)

// TestWriteAndRestore changes the runner's own system proxy, which CI allows (only CI: a
// developer's Windows machine keeps its settings), and puts it back. The impersonation
// around it needs SYSTEM, so the service's VM test covers that part.
func TestWriteAndRestore(t *testing.T) {
	if os.Getenv("GITHUB_ACTIONS") != "true" {
		t.Skip("changes the system proxy; runs on CI only")
	}
	before, err := read()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := write(before); err != nil {
			t.Errorf("restore %+v: %v", before, err)
		}
		if after, err := read(); err != nil || after != before {
			t.Errorf("after restoring: %+v, %v; want %+v", after, err, before)
		}
	})
	ours := Core(7890)
	if err := write(ours); err != nil {
		t.Fatal(err)
	}
	got, err := read()
	if err != nil {
		t.Fatal(err)
	}
	if got.Flags&Proxy == 0 || got.Server != "127.0.0.1:7890" || got.Bypass != Bypass {
		t.Fatalf("read %+v after writing %+v", got, ours)
	}
}
