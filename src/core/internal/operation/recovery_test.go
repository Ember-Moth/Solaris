package operation

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestWatchdogTerminatesOnlyItsChild(t *testing.T) {
	const child = "SOLARIS_WATCHDOG_TEST_CHILD"
	if os.Getenv(child) == "1" {
		executor := New(1, Terminate)
		_, _ = Run(executor, context.Background(), "test_mutation", 20*time.Millisecond, 0,
			func(context.Context) (int, error) { select {} })
		time.Sleep(2 * time.Second)
		os.Exit(99)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestWatchdogTerminatesOnlyItsChild$")
	command.Env = append(os.Environ(), child+"=1")
	output, err := command.CombinedOutput()
	if err == nil || ctx.Err() != nil {
		t.Fatalf("watchdog exit: %v, context: %v, output: %s", err, ctx.Err(), output)
	}
	if command.ProcessState.ExitCode() == 99 || !strings.Contains(string(output), "core watchdog: test_mutation") {
		t.Fatalf("host was not terminated with its diagnostic: %s", output)
	}
}
