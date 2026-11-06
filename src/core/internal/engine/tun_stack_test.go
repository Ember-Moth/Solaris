package engine

// UDP through mihomo's TUN stacks without a TUN on the machine. The test plays the system on
// the far side of the TUN: it writes IP packets into a device in memory, as an application's
// datagrams reach the TUN, and reads what the stack writes back. The stack, mihomo's TUN
// handler and the running engine are the ones the hosts use, put together as sing_tun.New
// puts them; only the device is a stand-in, shaped like the Windows service's wintun or the
// macOS extension's utun so the stack takes each host's way of reading and writing. The
// owned-TUN and fd-mode tests in CI run the same exchanges on a real TUN
// (tun_udp_linux_test.go).

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/listener/sing"
	"github.com/metacubex/mihomo/listener/sing_tun"
	"github.com/metacubex/mihomo/log"
	"github.com/metacubex/mihomo/tunnel"
	tun "github.com/metacubex/sing-tun"
	"github.com/metacubex/sing/common/buf"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
)

// memoryTUN is a TUN device in memory: in holds the system's packets for the stack, out
// the stack's packets for the system.
type memoryTUN struct {
	in, out chan []byte
	done    chan struct{}
	once    sync.Once
}

func newMemoryTUN() *memoryTUN {
	return &memoryTUN{in: make(chan []byte, 1024), out: make(chan []byte, 1024), done: make(chan struct{})}
}

func (d *memoryTUN) Read(p []byte) (int, error) {
	select {
	case packet := <-d.in:
		return copy(p, packet), nil
	case <-d.done:
		return 0, net.ErrClosed
	}
}

func (d *memoryTUN) Write(p []byte) (int, error) {
	select {
	case d.out <- bytes.Clone(p):
		return len(p), nil
	case <-d.done:
		return 0, net.ErrClosed
	}
}

func (d *memoryTUN) Close() error {
	d.once.Do(func() { close(d.done) })
	return nil
}

// wintunDevice reads as wintun does, a packet at a time from its ring.
type wintunDevice struct{ *memoryTUN }

func (d wintunDevice) ReadPacket() ([]byte, func(), error) {
	select {
	case packet := <-d.in:
		return packet, nil, nil
	case <-d.done:
		return nil, nil, net.ErrClosed
	}
}

// utunDevice reads as a utun with recvmsg_x does, in batches of buffers with the four-byte
// address family header ahead of each packet, and takes writes with that header: mihomo's
// defaults on macOS (recvmsgx on, sendmsgx off).
type utunDevice struct{ *memoryTUN }

const utunBatch = 16

// utunHeader is the address family ahead of every packet on a utun, on every system the
// test runs on (tun.PacketOffset is 4 on Darwin only).
const utunHeader = 4

func (d utunDevice) BatchSize() int { return utunBatch }

func (d utunDevice) BatchRead() ([]*buf.Buffer, error) {
	var buffers []*buf.Buffer
	add := func(packet []byte) {
		buffer := buf.NewSize(len(packet) + utunHeader)
		buffer.Extend(utunHeader)
		_, _ = buffer.Write(packet)
		buffer.Advance(utunHeader)
		buffers = append(buffers, buffer)
	}
	select {
	case packet := <-d.in:
		add(packet)
	case <-d.done:
		return nil, net.ErrClosed
	}
	for len(buffers) < utunBatch {
		select {
		case packet := <-d.in:
			add(packet)
		default:
			return buffers, nil
		}
	}
	return buffers, nil
}

func (d utunDevice) BatchWrite(buffers []*buf.Buffer) error {
	for _, buffer := range buffers {
		if _, err := d.memoryTUN.Write(buffer.Bytes()); err != nil {
			return err
		}
	}
	return nil
}

func (d utunDevice) Write(p []byte) (int, error) {
	if len(p) <= utunHeader {
		return 0, errors.New("utun write without a packet")
	}
	family, packet := p[:utunHeader], p[utunHeader:]
	want := byte(2) // AF_INET on Darwin
	if packet[0]>>4 == 6 {
		want = 30 // AF_INET6
	}
	if !bytes.Equal(family, []byte{0, 0, 0, want}) {
		return 0, fmt.Errorf("utun family header %x for an IPv%d packet", family, packet[0]>>4)
	}
	if _, err := d.memoryTUN.Write(packet); err != nil {
		return 0, err
	}
	return len(p), nil
}

// datagram is one UDP datagram as the system sees it on the TUN.
type datagram struct {
	source, destination netip.AddrPort
	payload             []byte
}

// ipChecksum is the Internet checksum of data, folded onto sum.
func ipChecksum(sum uint32, data []byte) uint16 {
	for ; len(data) >= 2; data = data[2:] {
		sum += uint32(binary.BigEndian.Uint16(data))
	}
	if len(data) == 1 {
		sum += uint32(data[0]) << 8
	}
	for sum>>16 != 0 {
		sum = sum&0xffff + sum>>16
	}
	return ^uint16(sum)
}

// pseudoHeaderSum starts the UDP checksum with the IP pseudo-header.
func pseudoHeaderSum(source, destination netip.Addr, length int) uint32 {
	var sum uint32
	for _, address := range [][]byte{source.AsSlice(), destination.AsSlice()} {
		for i := 0; i < len(address); i += 2 {
			sum += uint32(binary.BigEndian.Uint16(address[i:]))
		}
	}
	return sum + 17 + uint32(length)
}

func udpSegment(d datagram) []byte {
	segment := make([]byte, 8+len(d.payload))
	binary.BigEndian.PutUint16(segment[0:], d.source.Port())
	binary.BigEndian.PutUint16(segment[2:], d.destination.Port())
	binary.BigEndian.PutUint16(segment[4:], uint16(len(segment)))
	copy(segment[8:], d.payload)
	sum := ipChecksum(pseudoHeaderSum(d.source.Addr(), d.destination.Addr(), len(segment)), segment)
	if sum == 0 {
		sum = 0xffff
	}
	binary.BigEndian.PutUint16(segment[6:], sum)
	return segment
}

// packets writes d as the system sends it into a TUN of the given MTU: one IP packet, or
// fragments of at most mtu bytes when the datagram is larger.
func packets(d datagram, mtu int, id uint32) [][]byte {
	segment := udpSegment(d)
	source, destination := d.source.Addr(), d.destination.Addr()
	if source.Is4() {
		var out [][]byte
		step := (mtu - 20) &^ 7
		for offset := 0; offset == 0 || offset < len(segment); offset += step {
			end := min(offset+step, len(segment))
			packet := make([]byte, 20+end-offset)
			packet[0], packet[8], packet[9] = 0x45, 64, 17
			binary.BigEndian.PutUint16(packet[2:], uint16(len(packet)))
			binary.BigEndian.PutUint16(packet[4:], uint16(id))
			flags := uint16(offset / 8)
			if end < len(segment) {
				flags |= 0x2000
			}
			binary.BigEndian.PutUint16(packet[6:], flags)
			copy(packet[12:], source.AsSlice())
			copy(packet[16:], destination.AsSlice())
			binary.BigEndian.PutUint16(packet[10:], ipChecksum(0, packet[:20]))
			copy(packet[20:], segment[offset:end])
			out = append(out, packet)
		}
		return out
	}
	header := func(payloadLength int, next byte) []byte {
		packet := make([]byte, 40, 40+payloadLength)
		packet[0], packet[6], packet[7] = 0x60, next, 64
		binary.BigEndian.PutUint16(packet[4:], uint16(payloadLength))
		copy(packet[8:], source.AsSlice())
		copy(packet[24:], destination.AsSlice())
		return packet
	}
	if 40+len(segment) <= mtu {
		return [][]byte{append(header(len(segment), 17), segment...)}
	}
	var out [][]byte
	step := (mtu - 40 - 8) &^ 7
	for offset := 0; offset < len(segment); offset += step {
		end := min(offset+step, len(segment))
		fragment := make([]byte, 8)
		fragment[0] = 17
		field := uint16(offset)
		if end < len(segment) {
			field |= 1
		}
		binary.BigEndian.PutUint16(fragment[2:], field)
		binary.BigEndian.PutUint32(fragment[4:], id)
		packet := append(header(8+end-offset, 44), fragment...)
		out = append(out, append(packet, segment[offset:end]...))
	}
	return out
}

// tunReader takes the stack's packets off the device as the system's IP layer does: it checks
// each against the MTU and its checksums, puts fragments together and hands on the datagrams.
type tunReader struct {
	mtu       int
	datagrams chan datagram
	mu        sync.Mutex
	problems  []string
	partial   map[string]*partialDatagram
}

type partialDatagram struct {
	source, destination netip.Addr
	pieces              map[int][]byte
	total               int // the reassembled length once the last fragment is in, else -1
}

func readTUN(device *memoryTUN, mtu int) *tunReader {
	r := &tunReader{mtu: mtu, datagrams: make(chan datagram, 1024), partial: map[string]*partialDatagram{}}
	go func() {
		for {
			select {
			case packet := <-device.out:
				r.packet(packet)
			case <-device.done:
				return
			}
		}
	}()
	return r
}

func (r *tunReader) problem(format string, args ...any) {
	r.mu.Lock()
	r.problems = append(r.problems, fmt.Sprintf(format, args...))
	r.mu.Unlock()
}

func (r *tunReader) packet(packet []byte) {
	if len(packet) > r.mtu {
		r.problem("a %d-byte packet on a TUN with MTU %d", len(packet), r.mtu)
	}
	if len(packet) == 0 {
		return
	}
	switch packet[0] >> 4 {
	case 4:
		if len(packet) < 20 || ipChecksum(0, packet[:20]) != 0 {
			r.problem("an IPv4 header with a bad checksum")
			return
		}
		if packet[9] != 17 {
			return
		}
		source, _ := netip.AddrFromSlice(packet[12:16])
		destination, _ := netip.AddrFromSlice(packet[16:20])
		flags := binary.BigEndian.Uint16(packet[6:])
		key := fmt.Sprintf("4/%s/%s/%d", source, destination, binary.BigEndian.Uint16(packet[4:]))
		r.piece(key, source, destination, int(flags&0x1fff)*8, flags&0x2000 != 0, packet[20:binary.BigEndian.Uint16(packet[2:])])
	case 6:
		if len(packet) < 40 {
			return
		}
		source, _ := netip.AddrFromSlice(packet[8:24])
		destination, _ := netip.AddrFromSlice(packet[24:40])
		payload := packet[40 : 40+int(binary.BigEndian.Uint16(packet[4:]))]
		switch packet[6] {
		case 17:
			r.piece("6/"+source.String()+"/"+destination.String()+"/whole", source, destination, 0, false, payload)
		case 44:
			if payload[0] != 17 {
				return
			}
			field := binary.BigEndian.Uint16(payload[2:])
			key := fmt.Sprintf("6/%s/%s/%d", source, destination, binary.BigEndian.Uint32(payload[4:]))
			r.piece(key, source, destination, int(field&^7), field&1 != 0, payload[8:])
		}
	}
}

func (r *tunReader) piece(key string, source, destination netip.Addr, offset int, more bool, data []byte) {
	partial := r.partial[key]
	if partial == nil {
		partial = &partialDatagram{source: source, destination: destination, pieces: map[int][]byte{}, total: -1}
		r.partial[key] = partial
	}
	partial.pieces[offset] = bytes.Clone(data)
	if !more {
		partial.total = offset + len(data)
	}
	if partial.total < 0 {
		return
	}
	segment := make([]byte, 0, partial.total)
	for len(segment) < partial.total {
		next, ok := partial.pieces[len(segment)]
		if !ok {
			return
		}
		segment = append(segment, next...)
	}
	delete(r.partial, key)
	if len(segment) < 8 || int(binary.BigEndian.Uint16(segment[4:])) != len(segment) {
		r.problem("a UDP datagram whose length field does not match its %d bytes", len(segment))
		return
	}
	if stored := binary.BigEndian.Uint16(segment[6:]); stored != 0 || source.Is6() {
		if ipChecksum(pseudoHeaderSum(source, destination, len(segment)), segment) != 0 {
			r.problem("a %d-byte UDP datagram with a bad checksum", len(segment)-8)
			return
		}
	}
	r.datagrams <- datagram{
		source:      netip.AddrPortFrom(source, binary.BigEndian.Uint16(segment[0:])),
		destination: netip.AddrPortFrom(destination, binary.BigEndian.Uint16(segment[2:])),
		payload:     segment[8:],
	}
}

func (r *tunReader) takeProblems() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	problems := r.problems
	r.problems = nil
	return problems
}

// stackExchange sends every datagram into the TUN at once, as an application with that many
// datagrams in flight does, and waits for each echo: from the address it went to, back to
// the port it came from, with the same bytes. It returns how many came back. Reversed sends
// each datagram's fragments last first, as some systems do.
func stackExchange(device *memoryTUN, reader *tunReader, mtu int, sent []datagram, reversed bool) (int, error) {
	waiting := map[string]datagram{}
	for i, d := range sent {
		binary.BigEndian.PutUint64(d.payload, uint64(i))
		waiting[fmt.Sprint(i)] = d
		fragments := packets(d, mtu, uint32(i+1))
		if reversed {
			slices.Reverse(fragments)
		}
		for _, packet := range fragments {
			device.in <- packet
		}
	}
	deadline := time.After(5 * time.Second)
	for len(waiting) > 0 {
		select {
		case answer := <-reader.datagrams:
			if len(answer.payload) < 8 {
				continue
			}
			key := fmt.Sprint(binary.BigEndian.Uint64(answer.payload))
			d, ok := waiting[key]
			if !ok {
				continue
			}
			if answer.source != d.destination || answer.destination != d.source || !bytes.Equal(answer.payload, d.payload) {
				return len(sent) - len(waiting), fmt.Errorf("%d bytes to %s came back as %d bytes from %s to %s",
					len(d.payload), d.destination, len(answer.payload), answer.source, answer.destination)
			}
			delete(waiting, key)
		case <-deadline:
			return len(sent) - len(waiting), nil
		}
	}
	return len(sent), nil
}

func newDatagram(source, destination netip.AddrPort, size int) datagram {
	payload := make([]byte, max(size, 8))
	_, _ = rand.Read(payload[8:])
	return datagram{source: source, destination: destination, payload: payload}
}

// tunStack runs a stack on device with mihomo's TUN handler on the running engine's tunnel,
// as sing_tun.New does with the TUN's addresses and MTU in fd mode.
func tunStack(t *testing.T, name string, device tun.Tun, mtu uint32, recvMsgX bool) {
	t.Helper()
	handler, err := sing.NewListenerHandler(sing.ListenerConfig{Tunnel: tunnel.Tunnel, Type: C.TUN})
	if err != nil {
		t.Fatal(err)
	}
	network := admission.FDNetwork
	stack, err := tun.NewStack(name, tun.StackOptions{
		Context: context.Background(),
		Tun:     device,
		TunOptions: tun.Options{
			MTU:          mtu,
			Inet4Address: []netip.Prefix{network.Inet4},
			Inet6Address: []netip.Prefix{network.Inet6},
			EXP_RecvMsgX: recvMsgX,
		},
		UDPTimeout:  sing.UDPTimeout,
		ICMPTimeout: 30 * time.Second,
		Handler: &sing_tun.ListenerHandler{
			ListenerHandler: handler,
			DnsAddrPorts:    []netip.AddrPort{netip.AddrPortFrom(network.DNSServer(), 53)},
			Inet4Address:    []netip.Prefix{network.Inet4},
			Inet6Address:    []netip.Prefix{network.Inet6},
		},
		Logger: log.SingLogger,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := stack.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = stack.Close()
		_ = device.Close()
	})
}

// loopbackDatagramMax is the largest datagram the test's sockets on loopback send: macOS
// refuses UDP datagrams over net.inet.udp.maxdgram, 9216 bytes, with "message too long".
const loopbackDatagramMax = 9000

// TestUDPThroughTUNStacks sends UDP through the mips stack, which the settings default to,
// through a node: datagrams up to and past the TUN's MTU, so fragments go into the stack and
// come out of it, IPv6 among them, a burst like a QUIC flight and many flows at once. The
// MTU is 9000, mihomo's default and the fd mode's (admission.FDNetwork), and 1500, a TUN set
// to Ethernet's. gVisor needs the device's own link endpoint, which the stand-in lacks.
func TestUDPThroughTUNStacks(t *testing.T) {
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
	// The applications aim at addresses on the internet; the node relays them to the echo.
	remote4, remote6 := netip.MustParseAddr("203.0.113.10"), netip.MustParseAddr("2001:db8::10")
	tunnel := &nodeTunnel{addresses: map[netip.Addr]netip.Addr{remote4: echo.Addr(), remote6: echo.Addr()}}
	node := startNode(t, vlessNode(), tunnel)
	port := porttest.Free(t)
	if _, err := engine.Start(udpProfiles(t, node, port, echo, server.URL+"/generate_204")["subscription"]); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()

	devices := []struct {
		name     string
		make     func(*memoryTUN) tun.Tun
		recvMsgX bool
	}{
		{"device", func(d *memoryTUN) tun.Tun { return d }, false},
		{"wintun", func(d *memoryTUN) tun.Tun { return wintunDevice{d} }, false},
		{"utun", func(d *memoryTUN) tun.Tun { return utunDevice{d} }, true},
	}
	source4 := netip.AddrPortFrom(admission.FDNetwork.Inet4.Addr(), 40000)
	source6 := netip.AddrPortFrom(admission.FDNetwork.Inet6.Addr(), 40000)
	to4 := netip.AddrPortFrom(remote4, echo.Port())
	to6 := netip.AddrPortFrom(remote6, echo.Port())
	{
		for _, device := range devices {
			for _, mtu := range []int{int(admission.FDNetwork.MTU), 1500} {
				t.Run(fmt.Sprintf("%s/mtu %d", device.name, mtu), func(t *testing.T) {
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
					memory := newMemoryTUN()
					tunStack(t, "mips", device.make(memory), uint32(mtu), device.recvMsgX)
					reader := readTUN(memory, mtu)
					whole := mtu - 20 - 8
					type exchange struct {
						name     string
						sent     []datagram
						reversed bool
					}
					var cases []exchange
					fragmented := min(3*mtu, loopbackDatagramMax)
					for i, size := range []int{1, 1200, 1350, 1452, whole, whole + 1, fragmented} {
						cases = append(cases, exchange{name: fmt.Sprintf("IPv4 %d bytes", size), sent: []datagram{newDatagram(
							netip.AddrPortFrom(source4.Addr(), source4.Port()+uint16(i)), to4, size)}})
					}
					cases = append(cases, exchange{name: fmt.Sprintf("IPv4 %d bytes, last fragment first", fragmented),
						sent: []datagram{newDatagram(netip.AddrPortFrom(source4.Addr(), 40100), to4, fragmented)}, reversed: true})
					for i, size := range []int{1232, mtu - 40 - 8, mtu - 40 - 8 + 1, fragmented} {
						cases = append(cases, exchange{name: fmt.Sprintf("IPv6 %d bytes", size), sent: []datagram{newDatagram(
							netip.AddrPortFrom(source6.Addr(), source6.Port()+uint16(i)), to6, size)}})
					}
					var burst, flows []datagram
					for range 64 {
						burst = append(burst, newDatagram(netip.AddrPortFrom(source4.Addr(), 41000), to4, 1350))
					}
					for i := range 64 {
						flows = append(flows, newDatagram(netip.AddrPortFrom(source4.Addr(), 42000+uint16(i)), to4, 1200))
					}
					cases = append(cases, exchange{name: "burst of 64 from one flow", sent: burst},
						exchange{name: "64 flows at once", sent: flows})
					for _, c := range cases {
						got, err := stackExchange(memory, reader, mtu, c.sent, c.reversed)
						if err != nil {
							t.Errorf("%s: %v", c.name, err)
						} else if got != len(c.sent) {
							t.Errorf("%s: %d of %d came back", c.name, got, len(c.sent))
						}
						for _, problem := range reader.takeProblems() {
							t.Errorf("%s: %s", c.name, problem)
						}
					}
				})
			}
		}
	}
	if tunnel.datagram.Load() == 0 {
		t.Fatal("the datagrams went around the node")
	}
}
