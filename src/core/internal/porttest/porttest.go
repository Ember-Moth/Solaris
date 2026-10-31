// Package porttest finds loopback ports for tests that start inbounds.
package porttest

import (
	"math/rand/v2"
	"net"
	"strconv"
	"sync"
	"testing"
)

// Ports are drawn below the systems' ephemeral ranges (49152 up on macOS and Windows, 32768
// up on Linux), where every outgoing socket takes its port and can take this one before
// the test binds it. Windows also reserves ranges for UDP alone (Hyper-V, WinNAT), so a
// port free for TCP may still refuse the mixed port's UDP listener.
const (
	low  = 20000
	high = 32768
)

var (
	mu    sync.Mutex
	given = map[int]bool{}
)

// Free returns a port on 127.0.0.1 free for both TCP and UDP, as the mixed port needs,
// like tests/e2e/servers.py free_port. No two calls in a process return the same port.
func Free(t testing.TB) int {
	t.Helper()
	mu.Lock()
	defer mu.Unlock()
	for range 100 {
		port := low + rand.IntN(high-low)
		if given[port] || !bindable(port) {
			continue
		}
		given[port] = true
		return port
	}
	t.Fatal("no port on 127.0.0.1 is free for both TCP and UDP")
	return 0
}

func bindable(port int) bool {
	address := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))
	tcp, err := net.Listen("tcp", address)
	if err != nil {
		return false
	}
	defer tcp.Close()
	udp, err := net.ListenPacket("udp", address)
	if err != nil {
		return false
	}
	udp.Close()
	return true
}
