package engine

import (
	"context"
	"net"
	"sync"
	"time"

	"github.com/metacubex/mihomo/component/dialer"
	"github.com/metacubex/mihomo/component/resolver"
	C "github.com/metacubex/mihomo/constant"
)

// TCP delay tests (D32) time the TCP handshake with a node's server. mihomo has no such
// test and keeps no history for it, so the engine keeps the results until the next
// configuration applies.

const (
	// tcpTestConcurrency is how many handshakes a group's test runs at once.
	tcpTestConcurrency = 32
	// maxGroupNesting bounds the walk from a group down to the node it uses.
	maxGroupNesting = 8
)

// tcpTypes are the node types that always reach their server over TCP. The others have
// no TCP server to dial: Hysteria, Hysteria2, TUIC, WireGuard and ShadowQUIC run over UDP;
// Mieru, OpenVPN, MASQUE and TrustTunnel may, by their options; the mesh outbounds have no
// single server.
var tcpTypes = map[C.AdapterType]bool{
	C.Shadowsocks:  true,
	C.ShadowsocksR: true,
	C.Snell:        true,
	C.Socks5:       true,
	C.Http:         true,
	C.Vmess:        true,
	C.Vless:        true,
	C.Trojan:       true,
	C.Ssh:          true,
	C.AnyTLS:       true,
	C.Sudoku:       true,
}

// tcpResult is a node's last TCP test: the server it dialled and the delay in
// milliseconds, 0 when the handshake failed.
type tcpResult struct {
	addr  string
	delay uint32
}

// tcpNode is the node a TCP test of proxy dials: proxy itself, or the node a group uses
// now. There is none for a group that spreads its connections (load-balance), and none
// for what has no TCP server of its own (tcpTypes, DIRECT, REJECT) or reaches it through
// another proxy (dialer-proxy), whose path a direct handshake would not follow.
func tcpNode(proxy C.Proxy) (C.Proxy, bool) {
	for range maxGroupNesting {
		group, ok := groupOf(proxy)
		if !ok {
			return proxy, tcpTypes[proxy.Type()] && proxy.Addr() != "" && proxy.ProxyInfo().DialerProxy == ""
		}
		now := group.Now()
		proxy = nil
		for _, member := range group.Proxies() {
			if member.Name() == now {
				proxy = member
				break
			}
		}
		if proxy == nil {
			return nil, false
		}
	}
	return nil, false
}

func (e *Engine) clearTCPDelays() {
	e.tcpMu.Lock()
	e.tcpDelays = nil
	e.tcpMu.Unlock()
}

func (e *Engine) recordTCPDelay(node C.Proxy, delay uint32) {
	e.tcpMu.Lock()
	defer e.tcpMu.Unlock()
	if e.tcpDelays == nil {
		e.tcpDelays = map[string]tcpResult{}
	}
	e.tcpDelays[node.Name()] = tcpResult{addr: node.Addr(), delay: delay}
}

// tcpDelay is the last TCP result of proxy, or of the node a group uses now. A result
// for another server, as before a provider update, does not count.
func (e *Engine) tcpDelay(proxy C.Proxy) (uint32, bool) {
	node, ok := tcpNode(proxy)
	if !ok {
		return 0, false
	}
	e.tcpMu.Lock()
	result, ok := e.tcpDelays[node.Name()]
	e.tcpMu.Unlock()
	if !ok || result.addr != node.Addr() {
		return 0, false
	}
	return result.delay, true
}

// testTCP measures the members of groups, each node once, a few at a time. A node whose
// turn the deadline comes before keeps its last result; one it cuts short failed, as in
// mihomo's URL test.
func (e *Engine) testTCP(ctx context.Context, groups []C.Proxy) {
	seen := map[string]bool{}
	var nodes []C.Proxy
	for _, proxy := range groups {
		group, _ := groupOf(proxy)
		for _, member := range group.Proxies() {
			if node, ok := tcpNode(member); ok && !seen[node.Name()] {
				seen[node.Name()] = true
				nodes = append(nodes, node)
			}
		}
	}
	slots := make(chan struct{}, tcpTestConcurrency)
	var wg sync.WaitGroup
	for _, node := range nodes {
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
		}
		if ctx.Err() != nil {
			break
		}
		wg.Go(func() {
			defer func() { <-slots }()
			e.recordTCPDelay(node, dialTCP(ctx, node))
		})
	}
	wg.Wait()
}

// dialTCP times a handshake with node's server, dialled as mihomo's outbounds dial: on
// the interface they bind to, so the TUN does not take it. The name is looked up first,
// with the resolver the dial uses, so the delay leaves the lookup out. 0 is a failure.
func dialTCP(ctx context.Context, node C.Proxy) uint32 {
	addr := node.Addr()
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return 0
	}
	if _, err := resolver.LookupIPWithResolver(ctx, host, resolver.ProxyServerHostResolver); err != nil {
		return 0
	}
	info := node.ProxyInfo()
	var options []dialer.Option
	if info.Interface != "" {
		options = append(options, dialer.WithInterface(info.Interface))
	}
	if info.RoutingMark != 0 {
		options = append(options, dialer.WithRoutingMark(info.RoutingMark))
	}
	start := time.Now()
	conn, err := dialer.DialContext(ctx, "tcp", addr, options...)
	if err != nil {
		return 0
	}
	elapsed := time.Since(start)
	_ = conn.Close()
	return uint32(max(elapsed.Milliseconds(), 1))
}
