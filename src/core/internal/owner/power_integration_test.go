package owner

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata/geodatatest"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
	"github.com/Ember-Moth/Solaris/src/core/internal/power"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

// The actual dispatcher and mihomo runtime, but no TUN and an in-memory proxy platform.
// This verifies selected-node persistence across the exact suspend/resume path used on Windows.
func TestWakeRestoresSelectionWithTheRealCore(t *testing.T) {
	root, machine := t.TempDir(), newMachine()
	machine.sessions[1] = alice
	if err := SaveRecord(root, Record{SID: alice, Session: 1}); err != nil {
		t.Fatal(err)
	}
	host, home, err := Open(context.Background(), root, bundled(t), machine, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	if err := geodatatest.Write(home); err != nil {
		t.Fatal(err)
	}
	core, err := engine.New(engine.Options{HomeDir: home, Tun: admission.TunNone})
	if err != nil {
		t.Fatal(err)
	}
	dispatcher := control.New(control.Options{Engine: core, Host: host, HostMethods: true})
	defer func() { host.Close(); dispatcher.Close(); _ = core.Close() }()
	host.Attach(dispatcher)
	port := porttest.Free(t)
	config := fmt.Sprintf(`mixed-port: %d
mode: rule
proxies:
  - {name: direct-a, type: direct}
  - {name: direct-b, type: direct}
proxy-groups:
  - {name: OUT, type: select, proxies: [direct-a, direct-b]}
rules: ["MATCH,OUT"]
`, port)
	request := &controlpb.Request{ProtocolVersion: product.ProtocolVersion, Id: 1,
		Method: &controlpb.Request_Start{Start: &controlpb.StartRequest{
			Source:  &controlpb.StartRequest_ConfigYaml{ConfigYaml: config},
			Options: &controlpb.RunOptions{MixedPort: uint32(port), SystemProxyEnabled: true},
		}}}
	encoded, err := proto.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	response := &controlpb.Response{}
	if err := proto.Unmarshal(dispatcher.Handle(context.Background(), as(alice, 1), encoded), response); err != nil {
		t.Fatal(err)
	}
	if response.GetError() != nil {
		t.Fatal(response.GetError())
	}
	if err := core.SelectProxy("OUT", "direct-b"); err != nil {
		t.Fatal(err)
	}
	host.PowerChanged(power.Event{Cycle: 1, Sleeping: true})
	eventually(t, func() bool { return !dispatcher.Running() && !host.SystemProxyEnabled() })
	host.PowerChanged(power.Event{Cycle: 1})
	eventually(t, func() bool {
		state, _ := host.RecoveryStatus()
		return dispatcher.Running() && state == controlpb.RecoveryState_RECOVERY_STATE_NONE && host.SystemProxyEnabled()
	})
	group, err := core.GetGroup("OUT", controlpb.GroupSort_GROUP_SORT_UNSPECIFIED)
	if err != nil || group.GetNow() != "direct-b" {
		t.Fatalf("selection after wake: %v, %v", group, err)
	}
	if (kept{home}).resumeInterrupted() {
		t.Fatal("successful wake retained its crash-loop guard")
	}
}
