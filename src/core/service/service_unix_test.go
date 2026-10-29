//go:build darwin || linux

package main

import (
	"errors"
	"testing"
)

func TestDevelopmentServerRefusesRoot(t *testing.T) {
	if err := refuseRoot(0); !errors.Is(err, errRoot) {
		t.Fatalf("refuseRoot(0) = %v, want errRoot", err)
	}
	if err := refuseRoot(501); err != nil {
		t.Fatalf("refuseRoot(501) = %v, want nil", err)
	}
}
