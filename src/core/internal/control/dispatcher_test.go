package control

import (
	"context"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/engine"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/product"
)

func call(t *testing.T, dispatcher *Dispatcher, request *controlpb.Request) *controlpb.Response {
	t.Helper()
	payload, err := proto.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return decode(t, dispatcher.Handle(context.Background(), peer.Identity{}, payload))
}

func decode(t *testing.T, payload []byte) *controlpb.Response {
	t.Helper()
	var response controlpb.Response
	if err := proto.Unmarshal(payload, &response); err != nil {
		t.Fatal(err)
	}
	if response.GetProtocolVersion() != product.ProtocolVersion {
		t.Fatalf("response protocol version = %d", response.GetProtocolVersion())
	}
	return &response
}

func request(id uint64, method func(*controlpb.Request)) *controlpb.Request {
	value := &controlpb.Request{ProtocolVersion: product.ProtocolVersion, Id: id}
	method(value)
	return value
}

func getInfo(r *controlpb.Request) {
	r.Method = &controlpb.Request_GetInfo{GetInfo: &controlpb.GetInfoRequest{}}
}

func expectError(t *testing.T, response *controlpb.Response, id uint64, code string) {
	t.Helper()
	if response.GetId() != id {
		t.Fatalf("response id = %d, want %d", response.GetId(), id)
	}
	if got := response.GetError().GetCode(); got != code {
		t.Fatalf("error code = %q, want %q", got, code)
	}
}

func TestGetInfo(t *testing.T) {
	for _, host := range []bool{true, false} {
		dispatcher := New(Options{Engine: engine.Unlinked{}, HostMethods: host})
		response := call(t, dispatcher, request(7, getInfo))
		if response.GetId() != 7 {
			t.Fatalf("response id = %d, want 7", response.GetId())
		}
		info := response.GetGetInfo()
		want := controlpb.Ownership_OWNERSHIP_UNSPECIFIED
		if host {
			want = controlpb.Ownership_OWNERSHIP_AVAILABLE
		}
		if info.GetOwnership() != want || info.GetCoreVersion() != "unlinked" ||
			info.GetState() != controlpb.RunState_RUN_STATE_STOPPED ||
			info.GetProtocolVersion() != product.ProtocolVersion {
			t.Fatalf("host=%v info = %v", host, info)
		}
	}
}

func TestRejectsInvalidRequests(t *testing.T) {
	dispatcher := New(Options{Engine: engine.Unlinked{}, HostMethods: true})

	wrongVersion := request(3, getInfo)
	wrongVersion.ProtocolVersion = product.ProtocolVersion + 1
	expectError(t, call(t, dispatcher, wrongVersion), 3, CodeUnsupportedVersion)

	expectError(t, call(t, dispatcher, request(0, getInfo)), 0, CodeInvalidRequest)
	expectError(t, call(t, dispatcher, request(4, func(*controlpb.Request) {})), 4, CodeInvalidRequest)

	malformed := dispatcher.Handle(context.Background(), peer.Identity{}, []byte{0xff, 0xff})
	expectError(t, decode(t, malformed), 0, CodeInvalidRequest)
}

func TestHostMethodsDependOnHost(t *testing.T) {
	stop := func(r *controlpb.Request) {
		r.Method = &controlpb.Request_Stop{Stop: &controlpb.StopRequest{}}
	}
	service := New(Options{Engine: engine.Unlinked{}, HostMethods: true})
	extension := New(Options{Engine: engine.Unlinked{}})
	expectError(t, call(t, service, request(1, stop)), 1, CodeNotImplemented)
	expectError(t, call(t, extension, request(1, stop)), 1, CodeUnsupportedMethod)

	getStatus := func(r *controlpb.Request) {
		r.Method = &controlpb.Request_GetStatus{GetStatus: &controlpb.GetStatusRequest{}}
	}
	expectError(t, call(t, extension, request(2, getStatus)), 2, CodeNotImplemented)
}
