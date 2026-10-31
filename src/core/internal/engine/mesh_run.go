package engine

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"sync"

	"github.com/metacubex/mihomo/adapter"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/log"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
)

// Mesh outbounds run for as long as their configuration does (docs/mesh.md §10.4). mihomo
// starts one at its first connection and, when a reload replaces it, leaves the old one to
// the garbage collector, so a Tailscale or ZeroTier node could run twice on one state
// directory for a while. The engine keeps the one that runs when a reload leaves its
// settings as they were, and closes the others before the new configuration applies. It
// starts the networks the user added in the app as soon as it applies a configuration;
// the mesh outbounds a subscription or a profile brings start at their first connection,
// as mihomo has them, since nothing the user did asked them to join their networks.

// meshKinds are the mesh outbounds' types in the configuration.
var meshKinds = []string{"tailscale", "zerotier", "easytier"}

// meshOutbound is a mesh outbound of the running configuration.
type meshOutbound struct {
	name string
	// adapter is the outbound as mihomo wraps it, which a reload hands on to the next
	// configuration when the settings are the same.
	adapter     C.ProxyAdapter
	fingerprint string // the settings as the configuration wrote them

	mu       sync.Mutex
	id       string // the mesh id when a composition made it; a reload may change it
	startErr error  // why the last start failed; nil once it starts
}

func (m *meshOutbound) setID(id string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.id = id
}

func (m *meshOutbound) meshID() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.id
}

func (m *meshOutbound) setStartErr(err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.startErr = err
}

func (m *meshOutbound) lastStartErr() error {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.startErr
}

// startMesh starts a mesh outbound without making a connection: mihomo's three each start
// in DialContext before they wait for the network, so with a cancelled context the call
// comes back once the start is done. Tests replace it.
var startMesh = func(outbound C.ProxyAdapter) error {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	conn, err := outbound.DialContext(ctx, &C.Metadata{NetWork: C.TCP, Host: "mesh.invalid", DstPort: 9})
	if conn != nil {
		_ = conn.Close()
	}
	if err == nil || errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}

// meshFingerprints are the mesh outbounds among the configuration's proxies, in order, with
// their settings as canonical JSON.
func meshFingerprints(doc map[string]any) (names []string, fingerprints map[string]string) {
	fingerprints = map[string]string{}
	items, _ := doc["proxies"].([]any)
	for _, item := range items {
		proxy, ok := item.(map[string]any)
		if !ok {
			continue
		}
		name, _ := proxy["name"].(string)
		kind, _ := proxy["type"].(string)
		if name == "" || !slices.Contains(meshKinds, kind) {
			continue
		}
		// encoding/json writes mapping keys in order.
		data, err := json.Marshal(proxy)
		if err != nil {
			continue
		}
		names = append(names, name)
		fingerprints[name] = string(data)
	}
	return names, fingerprints
}

// meshRun is what applying a configuration does to the mesh outbounds.
type meshRun struct {
	order    []string
	next     map[string]*meshOutbound
	fresh    []*meshOutbound  // the app's networks, to start once applied
	replaced []C.ProxyAdapter // the running ones the configuration no longer has
	// networks are the ids of the composition's networks, whose state stays; nil when the
	// configuration is no composition, which leaves every state as it is.
	networks []string
}

// adoptMesh prepares the configuration's mesh outbounds before it is applied: an outbound
// whose settings are the ones running takes over the running outbound, and the one mihomo
// parsed for it closes before it ever starts.
func (e *Engine) adoptMesh(admitted *admission) *meshRun {
	order, fingerprints := meshFingerprints(admitted.doc)
	ids := map[string]string{}
	networks := map[string]bool{} // the composition's own networks, which have no source
	for _, mesh := range admitted.report.GetMesh() {
		ids[mesh.GetName()] = mesh.GetId()
		networks[mesh.GetName()] = mesh.GetSourceId() == ""
	}
	run := &meshRun{next: map[string]*meshOutbound{}, networks: admitted.networks}
	e.meshMu.Lock()
	running := e.mesh
	e.meshMu.Unlock()
	kept := map[string]bool{}
	for _, name := range order {
		proxy, ok := admitted.cfg.Proxies[name].(*adapter.Proxy)
		if !ok {
			continue
		}
		run.order = append(run.order, name)
		if old := running[name]; old != nil && old.fingerprint == fingerprints[name] {
			// Every group of the new configuration, GLOBAL among them, holds this Proxy, so
			// the running outbound goes into it rather than into the proxy table alone.
			parsed := proxy.ProxyAdapter
			proxy.ProxyAdapter = old.adapter
			_ = parsed.Close()
			old.setID(ids[name])
			run.next[name] = old
			kept[name] = true
			continue
		}
		outbound := &meshOutbound{name: name, adapter: proxy.ProxyAdapter, fingerprint: fingerprints[name], id: ids[name]}
		run.next[name] = outbound
		if networks[name] {
			run.fresh = append(run.fresh, outbound)
		}
	}
	for name, old := range running {
		if !kept[name] {
			run.replaced = append(run.replaced, old.adapter)
		}
	}
	return run
}

// closeReplaced closes the outbounds the configuration replaces, before it applies: once
// it does, a connection may start a successor on the state they hold.
func closeReplaced(run *meshRun) {
	for _, outbound := range run.replaced {
		_ = outbound.Close()
	}
}

// runMesh follows hub.ApplyConfig: the state of networks the composition no longer has
// goes, and the app's networks start.
func (e *Engine) runMesh(run *meshRun) {
	e.meshMu.Lock()
	e.mesh, e.meshOrder = run.next, run.order
	e.meshMu.Unlock()
	if run.networks != nil {
		pruneMeshState(e.home, run.networks)
	}
	// Taken now: a start still under way once the configuration has moved on keeps the
	// function it began with.
	start := startMesh
	for _, outbound := range run.fresh {
		e.meshStarts.Go(func() {
			if err := start(outbound.adapter); err != nil {
				log.Warnln("[engine] mesh outbound %s did not start: %s", outbound.name, err)
				outbound.setStartErr(err)
			}
		})
	}
}

// closeMesh closes every mesh outbound, as the configuration stops, and waits for the
// starts still under way.
func (e *Engine) closeMesh() {
	e.meshMu.Lock()
	running := e.mesh
	e.mesh, e.meshOrder = nil, nil
	e.meshMu.Unlock()
	for _, outbound := range running {
		_ = outbound.adapter.Close()
	}
	e.meshStarts.Wait()
}

// meshOutbounds are the running mesh outbounds in configuration order.
func (e *Engine) meshOutbounds() []*meshOutbound {
	e.meshMu.Lock()
	defer e.meshMu.Unlock()
	out := make([]*meshOutbound, 0, len(e.meshOrder))
	for _, name := range e.meshOrder {
		out = append(out, e.mesh[name])
	}
	return out
}

func (e *Engine) meshOutbound(name string) *meshOutbound {
	e.meshMu.Lock()
	defer e.meshMu.Unlock()
	return e.mesh[name]
}

// pruneMeshState removes the state directories under the home's mesh directory that
// belong to no network of keep: a removed network's login and identity go with it. Only
// directories named like a network id are touched.
func pruneMeshState(home string, keep []string) {
	root := filepath.Join(home, compose.MeshStateRoot)
	entries, err := os.ReadDir(root)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			log.Warnln("[engine] mesh state: %s", err)
		}
		return
	}
	for _, entry := range entries {
		name := entry.Name()
		if !entry.IsDir() || !compose.IsNetworkID(name) || slices.Contains(keep, name) {
			continue
		}
		if err := os.RemoveAll(filepath.Join(root, name)); err != nil {
			log.Warnln("[engine] mesh state of %s: %s", name, err)
		}
	}
}
