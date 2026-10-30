//go:build !darwin && !linux

package engine

import (
	"fmt"

	LC "github.com/metacubex/mihomo/listener/config"
)

// hostTUN stands in on hosts without fd mode; the Windows service creates its own TUN.
type hostTUN struct{}

func newHostTUN(int) (*hostTUN, error) {
	return nil, fmt.Errorf("%w: fd mode needs macOS or Linux", ErrUnsupported)
}

func (*hostTUN) prepare(*LC.Tun, LC.Tun) error { return nil }

func (*hostTUN) settle(LC.Tun) {}
