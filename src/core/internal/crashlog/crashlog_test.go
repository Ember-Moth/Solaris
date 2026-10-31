package crashlog

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCaptureInChild(t *testing.T) {
	if directory := os.Getenv("SOLARIS_CRASH_PROBE"); directory != "" {
		closeLog, err := Open(directory)
		if err != nil {
			panic(err)
		}
		if os.Getenv("SOLARIS_CRASH_MODE") == "panic" {
			panic("isolated crash probe")
		}
		closeLog()
		return
	}
	for _, mode := range []string{"normal", "panic"} {
		t.Run(mode, func(t *testing.T) {
			directory := t.TempDir()
			command := exec.Command(os.Args[0], "-test.run=^TestCaptureInChild$")
			command.Env = append(os.Environ(), "SOLARIS_CRASH_PROBE="+directory, "SOLARIS_CRASH_MODE="+mode)
			output, err := command.CombinedOutput()
			files, _ := filepath.Glob(filepath.Join(directory, "go-*.log"))
			if mode == "normal" {
				if err != nil || len(files) != 0 {
					t.Fatalf("normal exit: %v %v %s", err, files, output)
				}
			} else {
				if err == nil || len(files) != 1 {
					t.Fatalf("panic: %v %v %s", err, files, output)
				}
				data, _ := os.ReadFile(files[0])
				if !strings.Contains(string(data), "isolated crash probe") {
					t.Fatalf("missing panic: %s", data)
				}
			}
		})
	}
}

func TestRetention(t *testing.T) {
	directory := t.TempDir()
	now := time.Now()
	for i := 0; i < MaxFiles+3; i++ {
		path := filepath.Join(directory, fmt.Sprintf("go-%d-1.log", i))
		if err := os.WriteFile(path, []byte("report"), 0600); err != nil {
			t.Fatal(err)
		}
		stamp := now.Add(-time.Duration(i) * time.Second)
		_ = os.Chtimes(path, stamp, stamp)
	}
	keep := filepath.Join(directory, "unrelated.txt")
	_ = os.WriteFile(keep, []byte("keep"), 0600)
	if err := Prune(directory, now); err != nil {
		t.Fatal(err)
	}
	files, _ := filepath.Glob(filepath.Join(directory, "go-*.log"))
	if len(files) != MaxFiles {
		t.Fatalf("kept %d files", len(files))
	}
	if err := Prune(directory, now.Add(MaxAge+time.Second)); err != nil {
		t.Fatal(err)
	}
	files, _ = filepath.Glob(filepath.Join(directory, "go-*.log"))
	if len(files) != 0 {
		t.Fatal(files)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatal(err)
	}
}
