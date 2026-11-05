//go:build !windows

package owner

import "os"

// replaceFile renames from over to; elsewhere an open reader does not stop it.
func replaceFile(from, to string) error {
	return os.Rename(from, to)
}
