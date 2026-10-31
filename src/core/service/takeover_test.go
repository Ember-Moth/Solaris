package main

import (
	"net"
	"strings"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

// answer serves one request on conn with response, and hands back the request.
func answer(t *testing.T, conn net.Conn, response *controlpb.Response) <-chan *controlpb.Request {
	t.Helper()
	requests := make(chan *controlpb.Request, 1)
	go func() {
		defer close(requests)
		payload, err := frame.Read(conn, frame.KindRequest)
		if err != nil {
			t.Error(err)
			return
		}
		var request controlpb.Request
		if err := proto.Unmarshal(payload, &request); err != nil {
			t.Error(err)
			return
		}
		requests <- &request
		response.ProtocolVersion, response.Id = product.ProtocolVersion, request.GetId()
		raw, err := proto.Marshal(response)
		if err != nil {
			t.Error(err)
			return
		}
		if err := frame.Write(conn, frame.KindResponse, raw); err != nil {
			t.Error(err)
		}
	}()
	return requests
}

func failure(code string) *controlpb.Response {
	return &controlpb.Response{Result: &controlpb.Response_Error{Error: &controlpb.Error{Code: code, Message: "no"}}}
}

// The helper asks for a take-over (decision D5b); the service restarting for its new owner,
// or already being the caller's, means it worked.
func TestRequestTakeOver(t *testing.T) {
	for _, tc := range []struct {
		response *controlpb.Response
		fails    string
	}{
		{response: failure(control.CodeServiceRestarting)},
		{response: &controlpb.Response{Result: &controlpb.Response_Empty{Empty: &controlpb.Empty{}}}},
		{response: failure(control.CodeOwnedByOtherUser), fails: control.CodeOwnedByOtherUser},
		{response: &controlpb.Response{}, fails: "no answer"},
	} {
		client, server := net.Pipe()
		requests := answer(t, server, tc.response)
		err := requestTakeOver(client)
		client.Close()
		server.Close()
		request := <-requests
		if !request.GetClaim().GetTakeOver() || request.GetProtocolVersion() != product.ProtocolVersion {
			t.Fatalf("request = %v", request)
		}
		if tc.fails == "" && err != nil || tc.fails != "" && (err == nil || !strings.Contains(err.Error(), tc.fails)) {
			t.Fatalf("answered %v: %v", tc.response, err)
		}
	}
}
