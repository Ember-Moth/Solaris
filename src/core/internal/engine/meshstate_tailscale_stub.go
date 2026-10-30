//go:build !with_gvisor || no_tailscale

package engine

import (
	"context"

	"github.com/metacubex/mihomo/adapter/outbound"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// mihomo refuses Tailscale outbounds in this build, so none ever runs.

func tailscaleStatus(context.Context, *outbound.Tailscale, *controlpb.MeshStatus) error {
	return errNoTailscale
}

func tailscaleLogin(context.Context, *outbound.Tailscale) (string, error) {
	return "", errNoTailscale
}
