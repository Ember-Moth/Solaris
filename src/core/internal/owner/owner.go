// Package owner is the Windows service's owner (docs/architecture.md §5.4): who may use the
// core, their mihomo home, what the service keeps there to bring the tunnel back when they
// sign in again (§5.2), and their system proxy (§5.5).
//
// One service process serves one owner. mihomo opens cache.db in its home once per process
// (§3.2), so a claim by another user records them in owner.json and restarts the service,
// which opens the engine in their home. The package itself runs anywhere; the Windows parts
// come in through Platform.
package owner

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// The layout under the service's root, %ProgramData%\Solaris (§5.1).
const (
	recordFile = "owner.json"
	usersDir   = "users"
)

// Record is owner.json: the owner's SID and the session they last claimed or signed in from.
type Record struct {
	SID     string `json:"sid"`
	Session uint32 `json:"session"`
}

// ValidSID accepts the string form of a SID ("S-1-5-21-…"), which names the owner's home
// directory, so nothing else may get through.
func ValidSID(sid string) bool {
	parts, ok := strings.CutPrefix(sid, "S-1-")
	if !ok || parts == "" {
		return false
	}
	for part := range strings.SplitSeq(parts, "-") {
		if part == "" || len(part) > 10 || strings.Trim(part, "0123456789") != "" {
			return false
		}
	}
	return true
}

// Home is the owner's mihomo home under root.
func Home(root, sid string) string {
	return filepath.Join(root, usersDir, sid)
}

// LoadRecord reads owner.json; false when the service has no owner yet.
func LoadRecord(root string) (Record, bool, error) {
	raw, err := os.ReadFile(filepath.Join(root, recordFile))
	if errors.Is(err, fs.ErrNotExist) {
		return Record{}, false, nil
	}
	if err != nil {
		return Record{}, false, err
	}
	var record Record
	if err := json.Unmarshal(raw, &record); err != nil {
		return Record{}, false, fmt.Errorf("%s: %w", recordFile, err)
	}
	if !ValidSID(record.SID) {
		return Record{}, false, fmt.Errorf("%s: %q is not a SID", recordFile, record.SID)
	}
	return record, true, nil
}

// SaveRecord replaces owner.json.
func SaveRecord(root string, record Record) error {
	raw, err := json.Marshal(record)
	if err != nil {
		return err
	}
	return writeFile(filepath.Join(root, recordFile), raw)
}

// writeFile replaces path in one step: a new file that only this process opened, then a
// rename over the old one, so a crash leaves either version and never half of one.
func writeFile(path string, data []byte) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	_, err = temporary.Write(data)
	if err == nil {
		err = temporary.Sync()
	}
	if closeErr := temporary.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(temporary.Name(), path)
}
