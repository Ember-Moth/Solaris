// Package engine is the libclash surface the control dispatcher drives.
//
// The libclash engine package does not exist yet (docs/architecture.md §3.2 and §3.3);
// until it does, hosts run Unlinked, which reports a stopped core.
package engine

import "github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"

type Engine interface {
	CoreVersion() string
	State() controlpb.RunState
}

type Unlinked struct{}

func (Unlinked) CoreVersion() string { return "unlinked" }

func (Unlinked) State() controlpb.RunState { return controlpb.RunState_RUN_STATE_STOPPED }
