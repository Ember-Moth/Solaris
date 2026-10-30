// Package admission prepares a profile for mihomo's parser and checks what it parsed
// (docs/architecture.md §3.2): the user's YAML or the composed configuration, then the
// overrides, the patches for the host's TUN mode and the security items, which always come
// last. Verify then holds the parsed configuration to what the patches promised and fails
// closed.
//
// The package uses mihomo's types, constants and stateless parsers only. mihomo's runtime,
// its package-level state and its log among it, belongs to internal/engine, which parses
// between Prepare and Verify, since parsing touches that state too.
package admission

import (
	"bytes"
	"errors"
	"fmt"

	"github.com/metacubex/mihomo/common/yaml"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
)

var (
	ErrInvalidConfig = errors.New("invalid configuration")
	ErrUnsupported   = errors.New("not supported by this host")
)

// TunMode decides who owns the TUN and the routes (docs/architecture.md §3.1). It is
// fixed when the engine is created.
type TunMode int

const (
	// TunNone never creates a TUN and keeps every inbound on 127.0.0.1. The development
	// server runs on the developer's own machine and uses it (§11).
	TunNone TunMode = iota
	// TunOwned lets mihomo create the TUN and set auto-route, auto-detect-interface and
	// strict-route: the Windows service, and the TUN tests in CI.
	TunOwned
	// TunFD runs on the TUN descriptor the macOS extension passes in (engine.Options.TunFD);
	// the extension sets the addresses, routes and DNS, with the values in FDNetwork.
	TunFD
)

func (m TunMode) String() string {
	switch m {
	case TunNone:
		return "no-tun"
	case TunOwned:
		return "owned-tun"
	case TunFD:
		return "fd"
	default:
		return fmt.Sprintf("TunMode(%d)", int(m))
	}
}

// Host is what the pipeline knows of the host that runs the configuration. It comes from
// the engine, not the caller.
type Host struct {
	Tun TunMode
	// IncludeAllNetworks says that the fd-mode host's tunnel includes all networks
	// (engine.Options.IncludeAllNetworks, D19); outside fd mode it means nothing.
	IncludeAllNetworks bool
	// Warn is told of a change the pipeline makes beyond what the profile asked, as it
	// makes it, for the engine's log; nil drops them.
	Warn func(message string)
}

// RunOptions are the host's settings that the pipeline lays over the user's profile.
type RunOptions struct {
	TUN bool
	// MixedPort replaces the profile's mixed-port when non-zero.
	MixedPort uint16
	// AllowLAN opts in to listening beyond 127.0.0.1; without it every inbound stays on
	// loopback whatever the profile says.
	AllowLAN bool
	// Stack replaces the profile's tun.stack when set: mihomo's name for it, "system",
	// "mips", "gvisor" or "mixed" (§3.4).
	Stack string
}

// Profile is one submission from the UI: the configuration, the overrides and the options.
type Profile struct {
	// Config is a subscription's own YAML. Composition, when set, takes its place: the core
	// assembles the configuration from the node pool and a plan (docs/routing.md §8).
	Config      []byte
	Composition *compose.Composition
	// Overrides are YAML mappings merged in order, the global override first and then
	// the profile's own (decision D4, Merge only); see applyOverrides.
	Overrides [][]byte
	Options   RunOptions
}

// Prepared is a profile ready for mihomo's parser.
type Prepared struct {
	Host Host
	// Options are the profile's options as the host's mode leaves them: no TUN and no LAN
	// without one, the TUN always on in fd mode. Verify holds the parsed configuration to them.
	Options RunOptions
	// Document is the configuration as it goes to the parser, and YAML the same encoded.
	Document map[string]any
	YAML     []byte
	// Composed comes with a composition, nil for a profile's own YAML; so do Networks, the
	// ids of the composition's mesh networks, whose state stays in the home.
	Composed *compose.Result
	Networks []string
}

// Prepare runs a profile through the pipeline up to mihomo's parser: user YAML or the
// composed configuration → overrides → platform patches for the TUN mode → security items,
// which always come last. Its errors wrap ErrInvalidConfig or ErrUnsupported.
func Prepare(profile Profile, host Host) (*Prepared, error) {
	if profile.Options.TUN && host.Tun == TunNone {
		return nil, fmt.Errorf("%w: this host never creates a TUN", ErrUnsupported)
	}
	doc, composed, err := document(profile)
	if err != nil {
		return nil, invalidConfig(err)
	}
	if err := applyOverrides(doc, profile.Overrides); err != nil {
		return nil, invalidConfig(err)
	}
	options := profile.Options
	switch host.Tun {
	case TunNone:
		options.TUN, options.AllowLAN = false, false
		err = patchNoTUN(doc, options)
	case TunOwned:
		err = patchOwnedTUN(doc, options)
	case TunFD:
		// The extension only runs while its tunnel is up, so the TUN is always on.
		options.TUN = true
		err = patchFDTUN(doc, options, host)
	default:
		return nil, fmt.Errorf("%w: unknown TUN mode %v", ErrUnsupported, host.Tun)
	}
	if err == nil {
		err = dropSystemListeners(doc)
	}
	if err != nil {
		return nil, invalidConfig(err)
	}
	restricted, err := enforceSecurity(doc)
	if err != nil {
		return nil, invalidConfig(err)
	}
	raw, err := yaml.Marshal(doc)
	if err != nil {
		return nil, invalidConfig(err)
	}
	prepared := &Prepared{Host: host, Options: options, Document: doc, YAML: raw, Composed: composed}
	if composed != nil {
		prepared.Networks = []string{}
		for _, network := range profile.Composition.Mesh {
			prepared.Networks = append(prepared.Networks, network.GetId())
		}
		meshWarnings(composed.Report, restricted)
	}
	return prepared, nil
}

func invalidConfig(err error) error {
	return fmt.Errorf("%w: %w", ErrInvalidConfig, err)
}

// document is where the pipeline starts: the profile's YAML, or the composed configuration.
func document(profile Profile) (map[string]any, *compose.Result, error) {
	if profile.Composition == nil {
		doc, err := decodeDocument(profile.Config)
		return doc, nil, err
	}
	composed, err := compose.Compose(*profile.Composition)
	if err != nil {
		return nil, nil, err
	}
	return composed.Config, composed, nil
}

// decodeDocument reads the profile as a generic mapping. Decoding into a map resolves
// anchors and merge keys, so a patch cannot be bypassed by an alias.
func decodeDocument(source []byte) (map[string]any, error) {
	if len(bytes.TrimSpace(source)) == 0 {
		return nil, errors.New("the configuration is empty")
	}
	var doc map[string]any
	if err := yaml.Unmarshal(source, &doc); err != nil {
		return nil, err
	}
	if doc == nil {
		return nil, errors.New("the configuration is not a YAML mapping")
	}
	return doc, nil
}
