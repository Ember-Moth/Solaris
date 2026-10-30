//go:build cgo

package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"sync"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

// session is one attached tunnel: an engine in fd mode and its dispatcher.
type session struct {
	engine     *engine.Engine
	dispatcher *control.Dispatcher
	// ctx ends the requests in flight and the subscriptions when the tunnel detaches.
	ctx    context.Context
	cancel context.CancelFunc
}

var (
	// lifecycle serializes attach and detach.
	lifecycle sync.Mutex
	// mu guards current. handle holds the read lock while it dispatches, so that detach
	// waits for the requests in flight.
	mu      sync.RWMutex
	current *session
)

// attach seeds home with the GEO data in resources, unless resources is empty, then opens
// the engine and the dispatcher on the descriptor.
func attach(fd int, home, resources string) error {
	lifecycle.Lock()
	defer lifecycle.Unlock()
	mu.RLock()
	attached := current != nil
	mu.RUnlock()
	if attached {
		return errors.New("a tunnel is already attached")
	}
	if resources != "" {
		if err := seedGeoData(resources, home); err != nil {
			return err
		}
	}
	core, err := engine.New(engine.Options{HomeDir: home, Tun: engine.TunFD, TunFD: fd})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(context.Background())
	opened := &session{
		engine:     core,
		dispatcher: control.New(control.Options{Engine: core}),
		ctx:        ctx,
		cancel:     cancel,
	}
	mu.Lock()
	current = opened
	mu.Unlock()
	return nil
}

func detach() {
	lifecycle.Lock()
	defer lifecycle.Unlock()
	mu.RLock()
	detaching := current
	mu.RUnlock()
	if detaching == nil {
		return
	}
	detaching.cancel()
	mu.Lock()
	current = nil
	mu.Unlock()
	detaching.dispatcher.Close()
	// Close stops the core; mihomo closes the duplicate its TUN runs on.
	_ = detaching.engine.Close()
}

// handle answers one Request with the attached tunnel's dispatcher. The system restricts
// provider messages to this app, so there is no peer identity.
func handle(request []byte) []byte {
	mu.RLock()
	defer mu.RUnlock()
	if current == nil {
		return control.EncodeFailure(0, control.CodeNotRunning, "the extension has no tunnel attached")
	}
	return current.dispatcher.Handle(current.ctx, peer.Identity{}, request)
}

// network is engine.FDNetwork in the shape the extension's network settings take.
type network struct {
	IPv4Address      string `json:"ipv4_address"`
	IPv4SubnetMask   string `json:"ipv4_subnet_mask"`
	IPv6Address      string `json:"ipv6_address"`
	IPv6PrefixLength int    `json:"ipv6_prefix_length"`
	DNSServer        string `json:"dns_server"`
	MTU              uint32 `json:"mtu"`
}

func networkJSON() []byte {
	fd := engine.FDNetwork
	data, err := json.Marshal(network{
		IPv4Address:      fd.Inet4.Addr().String(),
		IPv4SubnetMask:   net.IP(net.CIDRMask(fd.Inet4.Bits(), 32)).String(),
		IPv6Address:      fd.Inet6.Addr().String(),
		IPv6PrefixLength: fd.Inet6.Bits(),
		DNSServer:        fd.DNSServer().String(),
		MTU:              fd.MTU,
	})
	if err != nil {
		panic(err)
	}
	return data
}
