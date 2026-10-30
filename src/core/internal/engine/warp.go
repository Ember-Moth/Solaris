package engine

import (
	"context"
	"fmt"

	"github.com/metacubex/mihomo/component/proxydialer"
	"github.com/metacubex/mihomo/tunnel"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/warp"
)

// warpAPI is where RegisterWarp registers; tests point it at a server of their own.
var warpAPI = warp.API

// RegisterWarp registers a WARP device, or gives an existing one a key for another
// protocol, through the running configuration's group via (docs/exits.md §3.2): Cloudflare
// sees the node's address, and the API is reachable where it is blocked. It holds no lock,
// since a registration takes seconds; a Reload meanwhile only changes the way out.
func (e *Engine) RegisterWarp(ctx context.Context, via string, request warp.Request) (*controlpb.WarpAccount, error) {
	if !e.running() {
		return nil, ErrNotRunning
	}
	if _, ok := tunnel.Proxies()[via]; !ok {
		return nil, fmt.Errorf("%w: the running configuration has no group %q", ErrInvalidArgument, via)
	}
	client := warp.NewClient(proxydialer.NewByName(via, tunnel.Tunnel).DialContext)
	defer client.CloseIdleConnections()
	return warp.Register(ctx, client, warpAPI, request)
}
