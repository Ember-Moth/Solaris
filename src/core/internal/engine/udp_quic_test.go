package engine

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/netip"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/transport/socks5"
	"github.com/metacubex/quic-go"
	"github.com/metacubex/tls"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// quicPort is where the QUIC client believes its server is: a port the plan's sniffer reads
// QUIC on (compose/base.yaml). The node relays it to the server's real port.
const quicPort = 443

// socksPacketConn is a UDP association as a net.PacketConn, for a QUIC client to dial
// through: every datagram goes to the one target, and answers seem to come from it. It
// keeps the socket out of sight, or quic-go would use the socket's own methods.
type socksPacketConn struct {
	net.PacketConn
	relay  *net.UDPAddr
	target *net.UDPAddr
}

func (c *socksPacketConn) WriteTo(p []byte, _ net.Addr) (int, error) {
	packet, err := socks5.EncodeUDPPacket(socks5.ParseAddr(c.target.String()), p)
	if err != nil {
		return 0, err
	}
	if _, err := c.PacketConn.WriteTo(packet, c.relay); err != nil {
		return 0, err
	}
	return len(p), nil
}

func (c *socksPacketConn) ReadFrom(p []byte) (int, net.Addr, error) {
	buffer := make([]byte, 65535)
	for {
		n, _, err := c.PacketConn.ReadFrom(buffer)
		if err != nil {
			return 0, nil, err
		}
		if _, payload, err := socks5.DecodeUDPPacket(buffer[:n]); err == nil {
			return copy(p, payload), c.target, nil
		}
	}
}

// quicEcho serves QUIC on loopback and echoes every stream.
func quicEcho(t *testing.T, certificate, key string) netip.AddrPort {
	t.Helper()
	pair, err := tls.X509KeyPair([]byte(certificate), []byte(key))
	if err != nil {
		t.Fatal(err)
	}
	server, err := quic.ListenAddr("127.0.0.1:0", &tls.Config{
		Certificates: []tls.Certificate{pair},
		NextProtos:   []string{"solaris-test"},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	t.Cleanup(func() {
		server.Close()
		wg.Wait()
	})
	wg.Go(func() {
		for {
			conn, err := server.Accept(context.Background())
			if err != nil {
				return
			}
			wg.Go(func() {
				// The client closes the connection once it has the echo; closing it here
				// would drop what it has not acknowledged yet.
				defer conn.CloseWithError(0, "")
				stream, err := conn.AcceptStream(context.Background())
				if err != nil {
					return
				}
				_, _ = io.Copy(stream, stream)
				_ = stream.Close()
				<-conn.Context().Done()
			})
		}
	})
	return server.Addr().(*net.UDPAddr).AddrPort()
}

// TestQUICThroughNode makes a QUIC connection through the mixed port's UDP association and a
// node, with the subscription's configuration and with "my plan", whose sniffer reads QUIC
// on the port. The client offers X25519MLKEM768, as browsers do, so its ClientHello spans
// two Initial packets, which the sniffer has to put together. The server name resolves
// nowhere: the sniffer keeps the address, so the connection does not depend on it.
func TestQUICThroughNode(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	var mu sync.Mutex
	var lines []string
	stopLogs := engine.WatchLogs(func(entry *controlpb.LogEntry) {
		mu.Lock()
		lines = append(lines, entry.GetMessage())
		mu.Unlock()
	})
	defer stopLogs()
	echo := udpEcho(t)
	server := target(t)
	credentials := newNodeCredentials(t)
	quicServer := quicEcho(t, credentials.certificate, credentials.key)
	seen := netip.AddrPortFrom(quicServer.Addr(), quicPort)
	// Not shadowsocks: under a burst of datagrams the race detector finds a race in its
	// server's NAT (sing's udpnat), which is not under test.
	var node udpNode
	for _, candidate := range udpNodes(credentials) {
		if candidate.name == "vless xudp" {
			node = candidate
		}
	}
	for _, routing := range []string{"subscription", "plan"} {
		t.Run(routing, func(t *testing.T) {
			mu.Lock()
			lines = nil
			mu.Unlock()
			defer func() {
				if t.Failed() {
					mu.Lock()
					t.Logf("mihomo log:\n%s", strings.Join(lines, "\n"))
					mu.Unlock()
				}
			}()
			tunnel := &nodeTunnel{ports: map[uint16]uint16{quicPort: quicServer.Port()}}
			outbound := startNode(t, node, tunnel)
			port := porttest.Free(t)
			if _, err := engine.Start(udpProfiles(t, outbound, port, echo, server.URL+"/generate_204")[routing]); err != nil {
				t.Fatal(err)
			}
			defer engine.Stop()
			association := associate(t, port)
			conn := &socksPacketConn{PacketConn: struct{ net.PacketConn }{association.conn}, relay: association.relay,
				target: net.UDPAddrFromAddrPort(seen)}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			client, err := quic.Dial(ctx, conn, conn.target, &tls.Config{
				ServerName:         nodeServerName,
				InsecureSkipVerify: true,
				NextProtos:         []string{"solaris-test"},
				CurvePreferences:   []tls.CurveID{tls.X25519MLKEM768, tls.X25519},
			}, nil)
			if err != nil {
				t.Fatalf("QUIC handshake through the node: %v", err)
			}
			defer client.CloseWithError(0, "")
			stream, err := client.OpenStreamSync(ctx)
			if err != nil {
				t.Fatal(err)
			}
			message := strings.Repeat("QUIC through Solaris ", 1000)
			if _, err := stream.Write([]byte(message)); err != nil {
				t.Fatal(err)
			}
			_ = stream.Close()
			answer, err := io.ReadAll(stream)
			if err != nil || string(answer) != message {
				t.Fatalf("echo: %d of %d bytes, %v", len(answer), len(message), err)
			}
			if tunnel.datagram.Load() == 0 {
				t.Fatal("QUIC went around the node")
			}
			if routing == "plan" && !sniffed(engine, nodeServerName) {
				t.Error(fmt.Sprintf("the plan's sniffer did not read %s from QUIC", nodeServerName))
			}
		})
	}
}

// sniffed says whether a UDP connection carries host as the name the sniffer read.
func sniffed(engine *Engine, host string) bool {
	for _, connection := range engine.Connections().GetConnections() {
		if connection.GetNetwork() == "udp" && connection.GetHost() == host {
			return true
		}
	}
	return false
}
