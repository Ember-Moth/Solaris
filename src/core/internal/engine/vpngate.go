package engine

import (
	"context"
	"fmt"

	"github.com/metacubex/mihomo/component/proxydialer"
	"github.com/metacubex/mihomo/tunnel"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/vpngate"
)

// vpnGateAPI is where ListVpnGate fetches the list; tests point it at a server of their own.
var vpnGateAPI = vpngate.API

// ListVpnGate fetches VPN Gate's server list through the running configuration's group via
// (docs/exits.md §5.2): VPN Gate sees the node's address, and the site is reachable where it
// is blocked. Like RegisterWarp it holds no lock while the list downloads.
func (e *Engine) ListVpnGate(ctx context.Context, via string) ([]*controlpb.VpnGateServer, error) {
	if !e.running() {
		return nil, ErrNotRunning
	}
	if _, ok := tunnel.Proxies()[via]; !ok {
		return nil, fmt.Errorf("%w: the running configuration has no group %q", ErrInvalidArgument, via)
	}
	client := vpngate.NewClient(proxydialer.NewByName(via, tunnel.Tunnel).DialContext)
	defer client.CloseIdleConnections()
	return vpngate.List(ctx, client, vpnGateAPI)
}
