package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/porttest"
	"github.com/Ember-Moth/Solaris/src/core/internal/warp"
)

// fakeWarpAPI answers registration on loopback with a fixed device and counts the calls.
func fakeWarpAPI(t *testing.T) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id":           "a1b2c3d4-0000-4000-8000-000000000001",
			"token":        "t0k3n",
			"warp_enabled": r.Method == http.MethodPatch,
			"config": map[string]any{
				"client_id": "U4An",
				"peers": []any{map[string]any{
					"public_key": "cGVlcg==",
					"endpoint":   map[string]any{"v4": "162.159.198.1:0"},
				}},
				"interface": map[string]any{"addresses": map[string]any{"v4": "172.16.0.2"}},
			},
		})
	}))
	t.Cleanup(server.Close)
	previous := warpAPI
	warpAPI = server.URL
	t.Cleanup(func() { warpAPI = previous })
	return server, &calls
}

// Registration goes out through the group it names: a group that rejects stops it.
func TestRegisterWarpGoesThroughTheGroup(t *testing.T) {
	engine := openEngine(t, admission.TunNone)
	_, calls := fakeWarpAPI(t)
	request := warp.Request{Protocol: controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2}
	if _, err := engine.RegisterWarp(context.Background(), "VIA", request); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("a stopped core: %v", err)
	}

	source := fmt.Sprintf(`mixed-port: %d
proxies:
  - {name: direct-a, type: direct}
proxy-groups:
  - {name: VIA, type: select, proxies: [direct-a]}
  - {name: BLOCKED, type: select, proxies: [REJECT]}
rules:
  - MATCH,VIA
`, porttest.Free(t))
	if _, err := engine.Start(admission.Profile{Config: []byte(source)}); err != nil {
		t.Fatal(err)
	}
	defer engine.Stop()

	account, err := engine.RegisterWarp(context.Background(), "VIA", request)
	if err != nil {
		t.Fatal(err)
	}
	if account.GetDeviceId() == "" || account.GetEndpoint() != "162.159.198.1:443" || calls.Load() != 2 {
		t.Fatalf("account %v after %d calls", account, calls.Load())
	}

	before := calls.Load()
	if _, err := engine.RegisterWarp(context.Background(), "BLOCKED", request); err == nil || calls.Load() != before {
		t.Fatalf("through a rejecting group: %v, %d calls", err, calls.Load()-before)
	}
	if _, err := engine.RegisterWarp(context.Background(), "NOPE", request); !errors.Is(err, ErrInvalidArgument) ||
		!strings.Contains(err.Error(), "NOPE") {
		t.Fatalf("an unknown group: %v", err)
	}
}
