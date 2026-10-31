//go:build windows && with_naive && with_purego

package main

import (
	"path/filepath"

	cronet "github.com/sagernet/cronet-go"
)

// Pin the privileged service to its installed DLL, never PATH. LoadLibrary remembers
// failures too, so a missing package cannot later fall back to an unrelated DLL.
func loadNativeRuntime(directory string) error {
	return cronet.LoadLibrary(filepath.Join(directory, "libcronet.dll"))
}
