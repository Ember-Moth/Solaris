// Package engine embeds mihomo for the control dispatcher (docs/architecture.md §3).
//
// The mihomo-backed implementation does not exist yet; until it does, hosts run
// Unlinked, which reports a stopped core.
package engine

import "github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"

type Engine interface {
	CoreVersion() string
	State() controlpb.RunState
}

type Unlinked struct{}

func (Unlinked) CoreVersion() string { return "unlinked" }

func (Unlinked) State() controlpb.RunState { return controlpb.RunState_RUN_STATE_STOPPED }
