package main

import (
	"errors"
	"fmt"
	"io"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/product"
)

// requestTakeOver asks the service on conn to stop another user's tunnel and make the user
// running this the owner (decision D5b, docs/architecture.md §5.4). The UI runs the helper
// that sends it elevated; the service then restarts for its new owner, so service_restarting
// is the answer that means it worked.
func requestTakeOver(conn io.ReadWriter) error {
	request, err := proto.Marshal(&controlpb.Request{
		ProtocolVersion: product.ProtocolVersion,
		Id:              1,
		Method:          &controlpb.Request_Claim{Claim: &controlpb.ClaimRequest{TakeOver: true}},
	})
	if err != nil {
		return err
	}
	if err := frame.Write(conn, frame.KindRequest, request); err != nil {
		return err
	}
	payload, err := frame.Read(conn, frame.KindResponse)
	if err != nil {
		return err
	}
	var response controlpb.Response
	if err := proto.Unmarshal(payload, &response); err != nil {
		return err
	}
	switch failure := response.GetError(); {
	case response.GetEmpty() != nil:
		return nil
	case failure.GetCode() == control.CodeServiceRestarting:
		return nil
	case failure != nil:
		return fmt.Errorf("%s: %s", failure.GetCode(), failure.GetMessage())
	}
	return errors.New("the service gave no answer to the take-over")
}
