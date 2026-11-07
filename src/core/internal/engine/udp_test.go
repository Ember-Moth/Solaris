package engine

// UDP end to end without a TUN: a client asks the mixed port for a SOCKS5 UDP association,
// as a game or a QUIC client pointed at the port does, and its datagrams go through a node
// to an echo server and back. The node is a mihomo inbound of the protocol under test,
// run apart from the engine with a tunnel of its own that relays to loopback only, so
// nothing leaves the machine. The TUN tests run the same exchanges through a TUN in CI
// (tun_udp_linux_test.go).

import (
	"bytes"
	"context"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"net/netip"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	N "github.com/metacubex/mihomo/common/net"
	"github.com/metacubex/mihomo/common/yaml"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/listener"
	"github.com/metacubex/mihomo/transport/socks5"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// echoHost is the name the UDP exchanges use for the echo server besides its address; the
// client's hosts and the node's own table both know it.
const echoHost = "echo.udp.test"

// fdPayloadMax is the largest UDP payload an IPv4 datagram carries unfragmented on the
// fd-mode TUN (admission.FDNetwork.MTU): what an app behind the macOS extension may send.
var fdPayloadMax = int(admission.FDNetwork.MTU) - 20 - 8

// udpPayloadSizes cover a byte, QUIC's initial datagram, a full Ethernet frame and the
// largest datagram the fd-mode TUN passes whole.
var udpPayloadSizes = []int{1, 1200, 1472, fdPayloadMax}

// testReadBuffer is the receive buffer of the echo server's and the node's sockets, which a
// burst of datagrams reaches at once: Windows gives a UDP socket 64 KB, less than the TUN
// tests' bursts of 64 datagrams, and a slow runner lost some of them there. Servers that
// carry QUIC raise theirs the same way.
const testReadBuffer = 4 << 20

// udpEcho answers every datagram with the same bytes, from the port it was sent to.
func udpEcho(t *testing.T) netip.AddrPort {
	t.Helper()
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := conn.SetReadBuffer(testReadBuffer); err != nil {
		t.Fatal(err)
	}
	go func() {
		buffer := make([]byte, 65535)
		for {
			n, from, err := conn.ReadFromUDPAddrPort(buffer)
			if err != nil {
				return
			}
			_, _ = conn.WriteToUDPAddrPort(buffer[:n], from)
		}
	}()
	return conn.LocalAddr().(*net.UDPAddr).AddrPort()
}

// nodeTunnel is the far side of a node: mihomo's inbound hands it what the client sent,
// and it relays that to loopback addresses only, or to remote too. It counts the datagrams
// it relayed, so a test can tell traffic that went through the node from traffic that went
// around it.
type nodeTunnel struct {
	hosts map[string]netip.Addr
	// remote is where the node relays besides loopback, and control binds the sockets that
	// reach it: the TUN tests' echo server, past the TUN.
	remote  netip.Prefix
	control func(network, address string, c syscall.RawConn) error
	// addresses and ports move destinations, so a client can aim at a well-known port, or
	// at an address on the internet, and reach a server on loopback.
	addresses map[netip.Addr]netip.Addr
	ports     map[uint16]uint16
	datagram  atomic.Int64

	mu       sync.Mutex
	sessions map[string]*nodeSession
	closed   bool
	wg       sync.WaitGroup
}

// nodeSession is the node's NAT for one client: a socket of its own, whose answers go back
// the way the client's first datagram came, from the address the client sent to.
type nodeSession struct {
	conn *net.UDPConn
	back C.UDPPacket
	mu   sync.Mutex
	seen map[netip.AddrPort]netip.AddrPort // relayed address -> the client's
}

// destination is where the node relays metadata's target, and the target as the client
// named it.
func (n *nodeTunnel) destination(metadata *C.Metadata) (relayed, seen netip.AddrPort, ok bool) {
	named := metadata.DstIP.Unmap()
	if !named.IsValid() {
		named = n.hosts[metadata.Host]
	}
	address := named
	if moved, ok := n.addresses[address]; ok {
		address = moved
	}
	if !address.IsValid() || !address.IsLoopback() && !n.remote.Contains(address) {
		return netip.AddrPort{}, netip.AddrPort{}, false
	}
	port := metadata.DstPort
	if moved, ok := n.ports[port]; ok {
		port = moved
	}
	return netip.AddrPortFrom(address, port), netip.AddrPortFrom(named, metadata.DstPort), true
}

func (n *nodeTunnel) HandleTCPConn(conn net.Conn, metadata *C.Metadata) {
	defer conn.Close()
	address, _, ok := n.destination(metadata)
	if !ok {
		return
	}
	dialer := net.Dialer{Timeout: 5 * time.Second, Control: n.control}
	remote, err := dialer.Dial("tcp", address.String())
	if err != nil {
		return
	}
	N.Relay(conn, remote)
}

// HandleUDPPacket relays the datagram on its client's session.
func (n *nodeTunnel) HandleUDPPacket(packet C.UDPPacket, metadata *C.Metadata) {
	defer packet.Drop()
	relayed, seen, ok := n.destination(metadata)
	if !ok {
		return
	}
	session := n.session(packet, metadata)
	if session == nil {
		return
	}
	n.datagram.Add(1)
	session.mu.Lock()
	session.seen[relayed] = seen
	session.mu.Unlock()
	_, _ = session.conn.WriteToUDPAddrPort(packet.Data(), relayed)
}

// session finds the client's session, or opens it with packet as the way back.
func (n *nodeTunnel) session(packet C.UDPPacket, metadata *C.Metadata) *nodeSession {
	key := metadata.SourceAddress() + "|" + packet.LocalAddr().String()
	n.mu.Lock()
	defer n.mu.Unlock()
	if session := n.sessions[key]; session != nil || n.closed {
		return session
	}
	local := "127.0.0.1:0"
	if n.remote.IsValid() {
		local = "0.0.0.0:0"
	}
	listen := net.ListenConfig{Control: n.control}
	socket, err := listen.ListenPacket(context.Background(), "udp4", local)
	if err != nil {
		return nil
	}
	session := &nodeSession{conn: socket.(*net.UDPConn), back: packet, seen: map[netip.AddrPort]netip.AddrPort{}}
	_ = session.conn.SetReadBuffer(testReadBuffer)
	if n.sessions == nil {
		n.sessions = map[string]*nodeSession{}
	}
	n.sessions[key] = session
	n.wg.Go(func() {
		defer session.conn.Close()
		buffer := make([]byte, 65535)
		for {
			size, from, err := session.conn.ReadFromUDPAddrPort(buffer)
			if err != nil {
				return
			}
			session.mu.Lock()
			if seen, ok := session.seen[from]; ok {
				from = seen
			}
			session.mu.Unlock()
			_, _ = session.back.WriteBack(buffer[:size], net.UDPAddrFromAddrPort(from))
		}
	})
	return session
}

// close ends the sessions, once the node's server no longer hands any over.
func (n *nodeTunnel) close() {
	n.mu.Lock()
	n.closed = true
	for _, session := range n.sessions {
		_ = session.conn.Close()
	}
	n.mu.Unlock()
	n.wg.Wait()
}

func (n *nodeTunnel) NatTable() C.NatTable { return nil }

// nodeCredentials are what the nodes' servers and the subscriptions share.
type nodeCredentials struct {
	certificate, key string // PEM, for node.test
	realityPrivate   string
	realityPublic    string
	realityDest      string // a TLS 1.3 server the REALITY servers borrow the handshake of
}

const (
	nodeUUID        = "7ba1fa49-6a0f-4d4b-8f4f-3a6f63a2e5d1"
	nodePassword    = "udp-test-password"
	realityShortID  = "0123456789abcdef"
	realityServer   = "reality.test"
	nodeServerName  = "node.test"
	snellPSK        = "udp-test-snell-psk"
	shadowsocks2022 = "MDEyMzQ1Njc4OWFiY2RlZg==" // 16 bytes, base64
)

func newNodeCredentials(t *testing.T) nodeCredentials {
	t.Helper()
	certificate, key := selfSigned(t, nodeServerName)
	pair, err := tls.X509KeyPair([]byte(certificate), []byte(key))
	if err != nil {
		t.Fatal(err)
	}
	borrowed, err := tls.Listen("tcp", "127.0.0.1:0", &tls.Config{
		Certificates: []tls.Certificate{pair},
		MinVersion:   tls.VersionTLS13,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { borrowed.Close() })
	go func() {
		for {
			conn, err := borrowed.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				_ = conn.SetDeadline(time.Now().Add(10 * time.Second))
				_ = conn.(*tls.Conn).Handshake()
				_, _ = conn.Read(make([]byte, 1))
			}()
		}
	}()
	private, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return nodeCredentials{
		certificate:    certificate,
		key:            key,
		realityPrivate: base64.RawURLEncoding.EncodeToString(private.Bytes()),
		realityPublic:  base64.RawURLEncoding.EncodeToString(private.PublicKey().Bytes()),
		realityDest:    borrowed.Addr().String(),
	}
}

func selfSigned(t *testing.T, name string) (string, string) {
	t.Helper()
	private, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: name},
		DNSNames:     []string{name},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &private.PublicKey, private)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(private)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})),
		string(pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}))
}

// udpNode is one protocol under test: its server as a listener mapping and the node as a
// subscription lists it, both without their name and address.
type udpNode struct {
	name     string
	inbound  map[string]any
	outbound map[string]any
	// maxPayload is the largest datagram the protocol carries whole, when it carries less than
	// the fd-mode TUN passes: a larger one comes back cut or not at all. Zero means every size
	// in udpPayloadSizes.
	maxPayload int
	// windowed protocols carry more than maxPayload once their record window has grown, so a
	// larger datagram may or may not pass.
	windowed bool
	// upstreamRace names a data race the race detector finds in a library this protocol runs
	// on; the case then runs only in the tests without -race, as CI also runs them.
	upstreamRace string
}

// tuicRace is in mihomo's TUIC v5 client: closing a UDP session clears its stream while a
// read still holds a copy of it (transport/tuic/v5 quicStreamPacketConn).
const tuicRace = "mihomo's tuic/v5 quicStreamPacketConn, between Close and WaitReadFrom"

func udpNodes(credentials nodeCredentials) []udpNode {
	tlsServer := map[string]any{"certificate": credentials.certificate, "private-key": credentials.key}
	with := func(base map[string]any, extra map[string]any) map[string]any {
		merged := map[string]any{}
		for _, part := range []map[string]any{base, extra} {
			for key, value := range part {
				merged[key] = value
			}
		}
		return merged
	}
	reality := map[string]any{"reality-config": map[string]any{
		"dest":         credentials.realityDest,
		"private-key":  credentials.realityPrivate,
		"short-id":     []any{realityShortID},
		"server-names": []any{realityServer},
	}}
	realityClient := map[string]any{
		"servername":         realityServer,
		"client-fingerprint": "chrome",
		"reality-opts":       map[string]any{"public-key": credentials.realityPublic, "short-id": realityShortID},
	}
	tlsClient := map[string]any{"skip-cert-verify": true}
	nodes := []udpNode{
		shadowsocksNode(),
		{
			name:     "shadowsocks 2022",
			inbound:  map[string]any{"type": "shadowsocks", "cipher": "2022-blake3-aes-128-gcm", "password": shadowsocks2022, "udp": true},
			outbound: map[string]any{"type": "ss", "cipher": "2022-blake3-aes-128-gcm", "password": shadowsocks2022, "udp": true},
		},
		{
			name:     "shadowsocks uot",
			inbound:  map[string]any{"type": "shadowsocks", "cipher": "aes-128-gcm", "password": nodePassword, "udp": true},
			outbound: map[string]any{"type": "ss", "cipher": "aes-128-gcm", "password": nodePassword, "udp": true, "udp-over-tcp": true},
		},
		{
			name:     "vmess",
			inbound:  map[string]any{"type": "vmess", "users": []any{map[string]any{"uuid": nodeUUID, "alterId": 0}}},
			outbound: map[string]any{"type": "vmess", "uuid": nodeUUID, "alterId": 0, "cipher": "auto", "udp": true},
		},
		vlessNode(),
		{
			name: "vless vision tls",
			inbound: with(tlsServer, map[string]any{"type": "vless",
				"users": []any{map[string]any{"uuid": nodeUUID, "flow": "xtls-rprx-vision"}}}),
			outbound: with(tlsClient, map[string]any{"type": "vless", "uuid": nodeUUID, "flow": "xtls-rprx-vision",
				"tls": true, "servername": nodeServerName, "udp": true}),
		},
		{
			name: "vless vision reality",
			inbound: with(reality, map[string]any{"type": "vless",
				"users": []any{map[string]any{"uuid": nodeUUID, "flow": "xtls-rprx-vision"}}}),
			outbound: with(realityClient, map[string]any{"type": "vless", "uuid": nodeUUID, "flow": "xtls-rprx-vision",
				"tls": true, "udp": true}),
		},
		{
			// mihomo cuts a datagram into packets of at most 8192 bytes (transport/trojan), and
			// the server sends each on as a datagram of its own.
			name:       "trojan",
			maxPayload: 8192,
			inbound:    with(tlsServer, map[string]any{"type": "trojan", "users": []any{map[string]any{"password": nodePassword}}}),
			outbound:   with(tlsClient, map[string]any{"type": "trojan", "password": nodePassword, "sni": nodeServerName, "udp": true}),
		},
		{
			// sing-quic drops UDP messages over the protocol's MaxUDPSize, 4096 bytes.
			name:         "hysteria2",
			maxPayload:   4096,
			upstreamRace: "sing's canceler.TimeoutPacketConn, between the server's read and write",
			inbound:      with(tlsServer, map[string]any{"type": "hysteria2", "users": map[string]any{"user": nodePassword}}),
			outbound:     with(tlsClient, map[string]any{"type": "hysteria2", "password": nodePassword, "sni": nodeServerName}),
		},
		{
			name:         "tuic native",
			upstreamRace: tuicRace,
			inbound: with(tlsServer, map[string]any{"type": "tuic", "users": map[string]any{nodeUUID: nodePassword},
				"alpn": []any{"h3"}}),
			outbound: with(tlsClient, map[string]any{"type": "tuic", "uuid": nodeUUID, "password": nodePassword,
				"sni": nodeServerName, "alpn": []any{"h3"}, "udp-relay-mode": "native"}),
		},
		{
			name:         "tuic quic",
			upstreamRace: tuicRace,
			inbound: with(tlsServer, map[string]any{"type": "tuic", "users": map[string]any{nodeUUID: nodePassword},
				"alpn": []any{"h3"}}),
			outbound: with(tlsClient, map[string]any{"type": "tuic", "uuid": nodeUUID, "password": nodePassword,
				"sni": nodeServerName, "alpn": []any{"h3"}, "udp-relay-mode": "quic"}),
		},
		{
			name:     "socks5",
			inbound:  map[string]any{"type": "socks", "udp": true},
			outbound: map[string]any{"type": "socks5", "udp": true},
		},
		{
			name:    "anytls reality",
			inbound: with(reality, map[string]any{"type": "anytls", "users": map[string]any{"user": nodePassword}}),
			outbound: map[string]any{"type": "anytls", "password": nodePassword, "sni": realityServer,
				"client-fingerprint": "chrome", "udp": true,
				"reality-opts": map[string]any{"public-key": credentials.realityPublic, "short-id": realityShortID}},
		},
	}
	for _, version := range []int{3, 4, 5, 6} {
		node := udpNode{
			name:     fmt.Sprintf("snell v%d", version),
			inbound:  map[string]any{"type": "snell", "psk": snellPSK, "version": version, "udp": true},
			outbound: map[string]any{"type": "snell", "psk": snellPSK, "version": version, "udp": true},
		}
		if version == 4 || version == 5 {
			// sing-snell paces v4 and v5 records like Surge: a datagram must fit the current
			// record, which grows by 1421 bytes a record and falls back to 1421 after 31 seconds
			// without one. The record also holds the command and the target address.
			node.maxPayload, node.windowed = 1421-1-7, true
		}
		nodes = append(nodes, node)
	}
	return nodes
}

// shadowsocksNode needs no certificate or key, for the tests that need one node of any kind.
func shadowsocksNode() udpNode {
	return udpNode{
		name:     "shadowsocks aes-128-gcm",
		inbound:  map[string]any{"type": "shadowsocks", "cipher": "aes-128-gcm", "password": nodePassword, "udp": true},
		outbound: map[string]any{"type": "ss", "cipher": "aes-128-gcm", "password": nodePassword, "udp": true},
	}
}

// vlessNode carries UDP as XUDP and needs no certificate either. Its server, unlike
// shadowsocks's, takes bursts of datagrams without a race (TestQUICThroughNode).
func vlessNode() udpNode {
	return udpNode{
		name:     "vless xudp",
		inbound:  map[string]any{"type": "vless", "users": []any{map[string]any{"uuid": nodeUUID}}, "allow-insecure": true},
		outbound: map[string]any{"type": "vless", "uuid": nodeUUID, "udp": true},
	}
}

// startNode runs a node's server on loopback and returns the node as a subscription lists it.
func startNode(t *testing.T, node udpNode, tunnel *nodeTunnel) map[string]any {
	t.Helper()
	// Inbounds that listen for TCP and UDP take both on the one port only when it is fixed.
	port := porttest.Free(t)
	mapping := map[string]any{"name": "udp-node-in", "listen": "127.0.0.1", "port": port}
	for key, value := range node.inbound {
		mapping[key] = value
	}
	inbound, err := listener.ParseListener(mapping)
	if err != nil {
		t.Fatalf("node server: %v", err)
	}
	if err := inbound.Listen(tunnel); err != nil {
		t.Fatalf("node server: %v", err)
	}
	t.Cleanup(func() {
		_ = inbound.Close()
		tunnel.close()
	})
	outbound := map[string]any{"name": "node", "server": "127.0.0.1", "port": port}
	for key, value := range node.outbound {
		outbound[key] = value
	}
	return outbound
}

// udpProfiles are the two ways a profile routes: a subscription's own configuration, with
// its own mixed port and LAN setting for the options to replace, and "my plan" composed
// from the same node. Both send everything to the node through their groups.
func udpProfiles(t *testing.T, node map[string]any, mixedPort int, echo netip.AddrPort, testURL string) map[string]admission.Profile {
	t.Helper()
	options := admission.RunOptions{MixedPort: uint16(mixedPort)}
	hosts := fmt.Sprintf("hosts: {%s: %s}\n", echoHost, echo.Addr())
	own, err := yaml.Marshal(map[string]any{
		"mixed-port": 7890,
		"allow-lan":  true,
		"mode":       "rule",
		"log-level":  "debug",
		"proxies":    []any{node},
		"proxy-groups": []any{map[string]any{
			"name": "PROXY", "type": "select", "proxies": []any{"node", "DIRECT"},
		}},
		"rules": []any{"MATCH,PROXY"},
	})
	if err != nil {
		t.Fatal(err)
	}
	source, err := yaml.Marshal(map[string]any{"proxies": []any{node}})
	if err != nil {
		t.Fatal(err)
	}
	plan := fmt.Sprintf(`{"version":1,"test_url":%q,"fallback":"proxy"}`, testURL)
	return map[string]admission.Profile{
		"subscription": {Config: append(own, hosts...), Options: options},
		"plan": {
			Composition: &compose.Composition{
				Sources: []compose.Source{{ID: "udp", Name: "udp", YAML: source}},
				Plan:    []byte(plan),
			},
			Overrides: [][]byte{[]byte(hosts + "log-level: debug\n")},
			Options:   options,
		},
	}
}

// udpAssociation is a SOCKS5 UDP association on the mixed port.
type udpAssociation struct {
	control net.Conn
	relay   *net.UDPAddr
	conn    *net.UDPConn
	seq     uint64
}

// associate asks the mixed port for a UDP association and checks the relay address its
// reply gives: the client sends its datagrams there, so it must be the port itself on the
// loopback address the inbound listens on.
func associate(t *testing.T, mixedPort int) *udpAssociation {
	t.Helper()
	control, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", mixedPort), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { control.Close() })
	_ = control.SetDeadline(time.Now().Add(5 * time.Second))
	bound, err := socks5.ClientHandshake(control, socks5.ParseAddr("0.0.0.0:0"), socks5.CmdUDPAssociate, nil)
	if err != nil {
		t.Fatalf("UDP ASSOCIATE: %v", err)
	}
	_ = control.SetDeadline(time.Time{})
	relay := bound.UDPAddr()
	if relay == nil || !relay.IP.Equal(net.IPv4(127, 0, 0, 1)) || relay.Port != mixedPort {
		t.Fatalf("UDP ASSOCIATE answered relay address %v, want 127.0.0.1:%d", bound, mixedPort)
	}
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return &udpAssociation{control: control, relay: relay, conn: conn}
}

// exchange sends size bytes to target through the association and waits for the echo, up
// to attempts times.
func (a *udpAssociation) exchange(target string, size, attempts int) error {
	a.seq++
	send := func(payload []byte) error {
		packet, err := socks5.EncodeUDPPacket(socks5.ParseAddr(target), payload)
		if err == nil {
			_, err = a.conn.WriteToUDP(packet, a.relay)
		}
		return err
	}
	buffer := make([]byte, 65535)
	receive := func(deadline time.Time) ([]byte, error) {
		_ = a.conn.SetReadDeadline(deadline)
		n, _, err := a.conn.ReadFromUDP(buffer)
		if err != nil {
			return nil, err
		}
		_, answer, err := socks5.DecodeUDPPacket(buffer[:n])
		return answer, err
	}
	if err := echoed(a.seq, size, attempts, send, receive); err != nil {
		return fmt.Errorf("%w to %s", err, target)
	}
	return nil
}

// echoed sends size bytes until receive gives them back, up to attempts times, as an
// application sends a lost datagram again. Each payload starts with the sequence number,
// so a late answer to an earlier send is told from a damaged one.
func echoed(seq uint64, size, attempts int, send func([]byte) error, receive func(time.Time) ([]byte, error)) error {
	payload := make([]byte, max(size, 8))[:size]
	_, _ = rand.Read(payload)
	if size >= 8 {
		binary.BigEndian.PutUint64(payload, seq)
	}
	for range attempts {
		if err := send(payload); err != nil {
			return fmt.Errorf("send %d bytes: %w", size, err)
		}
		deadline := time.Now().Add(2 * time.Second)
		for {
			answer, err := receive(deadline)
			if isTimeout(err) {
				break
			}
			if err != nil {
				return fmt.Errorf("answer to %d bytes: %w", size, err)
			}
			if size >= 8 && (len(answer) < 8 || binary.BigEndian.Uint64(answer) != seq) {
				continue // a late answer to an earlier send
			}
			if !bytes.Equal(answer, payload) {
				return fmt.Errorf("sent %d bytes, got %d back that differ", size, len(answer))
			}
			return nil
		}
	}
	return fmt.Errorf("no answer to %d bytes", size)
}

func isTimeout(err error) bool {
	var timeout interface{ Timeout() bool }
	return errors.As(err, &timeout) && timeout.Timeout()
}

// TestUDPThroughNodes sends UDP through each protocol's node, with the profile's own
// configuration and with "my plan", after the admission pipeline put the mixed port on
// 127.0.0.1 (restrictToLoopback, setMixedPort).
func TestUDPThroughNodes(t *testing.T) {
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
	for _, node := range udpNodes(credentials) {
		for _, routing := range []string{"subscription", "plan"} {
			t.Run(node.name+"/"+routing, func(t *testing.T) {
				if raceDetector && node.upstreamRace != "" {
					t.Skip("the race detector finds a race in " + node.upstreamRace)
				}
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
				tunnel := &nodeTunnel{hosts: map[string]netip.Addr{echoHost: echo.Addr()}}
				outbound := startNode(t, node, tunnel)
				port := porttest.Free(t)
				profile := udpProfiles(t, outbound, port, echo, server.URL+"/generate_204")[routing]
				if _, err := engine.Start(profile); err != nil {
					t.Fatal(err)
				}
				defer engine.Stop()
				association := associate(t, port)
				for _, size := range udpPayloadSizes {
					if node.maxPayload == 0 || size <= node.maxPayload {
						if err := association.exchange(echo.String(), size, 5); err != nil {
							t.Error(err)
						}
						continue
					}
					// Beyond the protocol's limit one send is enough: the answer never comes back whole.
					if err := association.exchange(echo.String(), size, 1); err == nil && !node.windowed {
						t.Errorf("%d bytes came back whole, beyond the protocol's %d", size, node.maxPayload)
					}
				}
				if err := association.exchange(net.JoinHostPort(echoHost, fmt.Sprint(echo.Port())), 64, 5); err != nil {
					t.Errorf("by name: %v", err)
				}
				if tunnel.datagram.Load() == 0 {
					t.Error("the datagrams went around the node")
				}
			})
		}
	}
}

// TestUDPWithoutNodeSupportGoesDirect pins mihomo's rule matching for a node that does not
// carry UDP (no udp: true): the rule that sends UDP to it is skipped, and with no rule after
// it the datagrams go out DIRECT, around the node, without a word above debug level.
func TestUDPWithoutNodeSupportGoesDirect(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	echo := udpEcho(t)
	server := target(t)
	node := shadowsocksNode()
	delete(node.outbound, "udp")
	for _, routing := range []string{"subscription", "plan"} {
		t.Run(routing, func(t *testing.T) {
			tunnel := &nodeTunnel{hosts: map[string]netip.Addr{echoHost: echo.Addr()}}
			outbound := startNode(t, node, tunnel)
			port := porttest.Free(t)
			if _, err := engine.Start(udpProfiles(t, outbound, port, echo, server.URL+"/generate_204")[routing]); err != nil {
				t.Fatal(err)
			}
			defer engine.Stop()
			if err := associate(t, port).exchange(echo.String(), 64, 5); err != nil {
				t.Fatal(err)
			}
			if got := tunnel.datagram.Load(); got != 0 {
				t.Fatalf("the node relayed %d datagrams", got)
			}
			var chains [][]string
			for _, connection := range engine.Connections().GetConnections() {
				if connection.GetNetwork() == "udp" {
					chains = append(chains, connection.GetChains())
				}
			}
			if len(chains) != 1 || len(chains[0]) != 1 || chains[0][0] != "DIRECT" {
				t.Fatalf("UDP chains %v, want one DIRECT", chains)
			}
		})
	}
}
