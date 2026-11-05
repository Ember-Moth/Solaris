//go:build windows

package owner

import (
	"errors"
	"os"
	"time"

	"golang.org/x/sys/windows"
)

// replaceAttempts and replaceRetry bound how long a write waits for a reader to let go.
const (
	replaceAttempts = 50
	replaceRetry    = 10 * time.Millisecond
)

// replaceFile renames from over to. Windows refuses to replace a file while a handle without
// FILE_SHARE_DELETE has it open, as Go's own readers do, so a read of state.json at the wrong
// moment would lose a Stop's was_running and bring the tunnel back after a restart. The
// reader lets go in a moment; try again until it has.
func replaceFile(from, to string) error {
	var err error
	for range replaceAttempts {
		if err = os.Rename(from, to); err == nil || !heldOpen(err) {
			return err
		}
		time.Sleep(replaceRetry)
	}
	return err
}

func heldOpen(err error) bool {
	return errors.Is(err, windows.ERROR_ACCESS_DENIED) || errors.Is(err, windows.ERROR_SHARING_VIOLATION)
}
