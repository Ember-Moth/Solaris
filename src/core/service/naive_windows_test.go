//go:build windows && with_naive && with_purego

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	cronet "github.com/Ember-Moth/cronet-go"
)

func TestNaiveRuntimePinnedToInstallDirectory(t *testing.T) {
	// The loader remembers its first result process-wide. Keep the failure probe
	// isolated from other tests, just as a service start has its own loader state.
	const probe = "SOLARIS_TEST_NAIVE_LOADER"
	if os.Getenv(probe) != "1" {
		command := exec.Command(os.Args[0], "-test.run=^TestNaiveRuntimePinnedToInstallDirectory$")
		command.Env = append(os.Environ(), probe+"=1")
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("loader probe: %v\n%s", err, output)
		}
		return
	}
	directory := t.TempDir()
	err := loadNativeRuntime(directory)
	if err == nil || !strings.Contains(err.Error(), filepath.Join(directory, "libcronet.dll")) {
		t.Fatalf("missing installed library must fail at its absolute path: %v", err)
	}
	// This is the binding imported by the fork's Naive adapter. A service loader
	// accidentally linked to the old binding would leave this loader uninitialized,
	// allowing it to search PATH instead of retaining the original failure.
	if again := cronet.LoadLibrary(""); again != err {
		t.Fatalf("service and Naive must share the same pinned loader: first=%v, next=%v", err, again)
	}
}
