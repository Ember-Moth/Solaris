package control

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/engine"
)

// meshEngine answers the mesh methods with fixed results.
type meshEngine struct {
	warpEngine
	statuses []*controlpb.MeshStatus
	login    string
	err      error
	asked    string
}

func (e *meshEngine) MeshStatus(context.Context) ([]*controlpb.MeshStatus, error) {
	return e.statuses, e.err
}

func (e *meshEngine) LoginMesh(_ context.Context, name string) (string, error) {
	e.asked = name
	return e.login, e.err
}

func getMeshStatus(id uint64) *controlpb.Request {
	return request(id, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetMeshStatus{GetMeshStatus: &controlpb.GetMeshStatusRequest{}}
	})
}

func loginMesh(id uint64, name string) *controlpb.Request {
	return request(id, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_LoginMesh{LoginMesh: &controlpb.LoginMeshRequest{Name: name}}
	})
}

func TestGetMeshStatus(t *testing.T) {
	statuses := []*controlpb.MeshStatus{{
		Name: "家里", Id: "mesh:ts1", Type: "Tailscale", State: controlpb.MeshState_MESH_STATE_ONLINE,
		Addresses: []string{"100.101.102.103/32"}, PeerCount: 3, NetworkName: "example.ts.net",
	}}
	fake := &meshEngine{statuses: statuses}
	dispatcher := New(Options{Engine: fake})
	response := call(t, dispatcher, getMeshStatus(1))
	got := response.GetGetMeshStatus().GetNetworks()
	if len(got) != 1 || !proto.Equal(got[0], statuses[0]) {
		t.Fatalf("get_mesh_status = %v", response)
	}
	fake.err = fmt.Errorf("%w", engine.ErrNotRunning)
	expectError(t, call(t, dispatcher, getMeshStatus(2)), 2, CodeNotRunning)
}

func TestLoginMesh(t *testing.T) {
	fake := &meshEngine{login: "https://login.tailscale.com/a/1234"}
	dispatcher := New(Options{Engine: fake})
	response := call(t, dispatcher, loginMesh(1, "家里"))
	if response.GetLoginMesh().GetLoginUrl() != fake.login || fake.asked != "家里" {
		t.Fatalf("login_mesh = %v, asked for %q", response, fake.asked)
	}
	expectError(t, call(t, dispatcher, loginMesh(2, "")), 2, CodeInvalidRequest)

	fake.err = fmt.Errorf("%w: 实验室 has no login", engine.ErrInvalidArgument)
	expectError(t, call(t, dispatcher, loginMesh(3, "实验室")), 3, CodeInvalidRequest)
	// Tailscale that took too long is worth another try.
	fake.err = errors.New("Tailscale gave no login URL in time")
	failed := call(t, dispatcher, loginMesh(4, "家里"))
	expectError(t, failed, 4, CodeOperationFailed)
	if !failed.GetError().GetRetryable() {
		t.Fatalf("error = %v", failed.GetError())
	}
}

// The mesh methods ask the running core, on every host.
func TestMeshMethodsNeedARunningCore(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: core, HostMethods: host})
		expectError(t, call(t, dispatcher, getMeshStatus(1)), 1, CodeNotRunning)
		expectError(t, call(t, dispatcher, loginMesh(2, "家里")), 2, CodeNotRunning)
		dispatcher.Close()
	}
}

// The networks ride in every composition the UI sends.
func TestCompositionCarriesTheNetworks(t *testing.T) {
	fake := &meshEngine{}
	dispatcher := New(Options{Engine: fake, HostMethods: true})
	network := &controlpb.MeshNetwork{Id: "ts1", Name: "家里",
		Network: &controlpb.MeshNetwork_Tailscale{Tailscale: &controlpb.TailscaleNetwork{AuthKey: "tskey-auth-x"}}}
	composition := compositionOf(`{"version": 1}`)
	composition.Mesh = []*controlpb.MeshNetwork{network}
	call(t, dispatcher, request(1, func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Validate{Validate: &controlpb.ValidateRequest{
			Source: &controlpb.ValidateRequest_Composition{Composition: composition},
		}}
	}))
	if fake.profile == nil || len(fake.profile.Composition.Mesh) != 1 || !proto.Equal(fake.profile.Composition.Mesh[0], network) {
		t.Fatalf("profile = %+v", fake.profile)
	}
}
