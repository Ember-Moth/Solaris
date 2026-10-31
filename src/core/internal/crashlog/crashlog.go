// Package crashlog keeps runtime panic/fatal output on the local machine. A service
// uses the current owner's protected home; system-extension output stays in its container.
package crashlog

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"sort"
	"time"
)

const (
	MaxFiles       = 20
	MaxBytes int64 = 64 << 20
	MaxAge         = 30 * 24 * time.Hour
)

var namePattern = regexp.MustCompile(`^go-[0-9-]+\.log$`)
var nativePattern = regexp.MustCompile(`(?i)^solaris-service\.exe\.[0-9]+\.dmp$`)

// Open installs the one runtime crash sink. The caller must only call it once per
// process and close it on normal shutdown. No header is written, so a zero-length
// file is a healthy process, not a crash. SetCrashOutput owns a duplicate descriptor.
func Open(directory string) (func(), error) {
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("crash directory is not a directory")
	}
	if err := Prune(directory, time.Now()); err != nil {
		return nil, err
	}
	name := filepath.Join(directory, fmt.Sprintf("go-%d-%d.log", time.Now().UnixNano(), os.Getpid()))
	file, err := os.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	err = debug.SetCrashOutput(file, debug.CrashOptions{})
	_ = file.Close()
	if err != nil {
		_ = os.Remove(name)
		return nil, err
	}
	return func() {
		_ = debug.SetCrashOutput(nil, debug.CrashOptions{})
		if info, err := os.Lstat(name); err == nil && info.Size() == 0 {
			_ = os.Remove(name)
		}
	}, nil
}

// Prune applies count, age and disk limits on startup. It only removes our regular
// files and never traverses a link. A fatal write may exceed the budget until restart.
func Prune(directory string, now time.Time) error {
	return prune(directory, now, namePattern, MaxFiles)
}

// PruneNative handles only the service's dedicated WER directory, never global OS dumps.
func PruneNative(directory string, now time.Time) error {
	return prune(directory, now, nativePattern, 5)
}

func prune(directory string, now time.Time, pattern *regexp.Regexp, limit int) error {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return err
	}
	var files []os.FileInfo
	for _, entry := range entries {
		if !pattern.MatchString(entry.Name()) || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		info, err := entry.Info()
		if err == nil && info.Mode().IsRegular() {
			files = append(files, info)
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].ModTime().After(files[j].ModTime()) })
	var size int64
	count := 0
	for _, file := range files {
		stale := now.Sub(file.ModTime()) > MaxAge
		empty := file.Size() == 0 && now.Sub(file.ModTime()) > 24*time.Hour
		if stale || empty || count >= limit || size+file.Size() > MaxBytes {
			if err := os.Remove(filepath.Join(directory, file.Name())); err != nil {
				return err
			}
		} else if file.Size() > 0 {
			count++
			size += file.Size()
		}
	}
	return nil
}
