// Package control decodes control Requests, runs the method and encodes the Response.
// The Windows service and the macOS extension share it (docs/architecture.md §4).
package control

import (
	"context"
	"fmt"
	"strings"

	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/controlpb"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/frame"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/product"
)

const (
	CodeInvalidRequest     = "invalid_request"
	CodeUnsupportedVersion = "unsupported_protocol_version"
	CodeUnsupportedMethod  = "unsupported_method"
	CodeNotImplemented     = "not_implemented"
	codeInternal           = "internal_error"
)

// Engine is what the dispatcher needs from internal/engine.
type Engine interface {
	CoreVersion() string
	State() controlpb.RunState
}

type Options struct {
	Engine Engine
	// HostMethods enables the Windows-service lifecycle methods. The macOS extension
	// leaves it off because NETunnelProviderManager starts and stops the tunnel.
	HostMethods bool
}

type Dispatcher struct {
	engine      Engine
	hostMethods bool
}

func New(options Options) *Dispatcher {
	return &Dispatcher{engine: options.Engine, hostMethods: options.HostMethods}
}

// Handle answers one encoded Request. Every failure, including an undecodable request,
// becomes an Error result, so the caller always has a Response to send back.
func (d *Dispatcher) Handle(ctx context.Context, caller peer.Identity, payload []byte) []byte {
	if len(payload) > frame.MaxRequestBytes {
		return EncodeFailure(0, CodeInvalidRequest, "request exceeds the size limit")
	}
	var request controlpb.Request
	if err := proto.Unmarshal(payload, &request); err != nil {
		return EncodeFailure(0, CodeInvalidRequest, "malformed request")
	}
	id := request.GetId()
	switch {
	case request.GetProtocolVersion() != product.ProtocolVersion:
		return EncodeFailure(id, CodeUnsupportedVersion, fmt.Sprintf(
			"protocol version %d is not supported; this side speaks %d",
			request.GetProtocolVersion(), product.ProtocolVersion))
	case id == 0:
		return EncodeFailure(0, CodeInvalidRequest, "request id must be non-zero")
	case request.Method == nil:
		return EncodeFailure(id, CodeInvalidRequest, "request names no known method")
	}
	return encode(id, d.dispatch(ctx, caller, &request))
}

func (d *Dispatcher) dispatch(_ context.Context, _ peer.Identity, request *controlpb.Request) *controlpb.Response {
	switch request.Method.(type) {
	case *controlpb.Request_GetInfo:
		return &controlpb.Response{Result: &controlpb.Response_GetInfo{GetInfo: d.info()}}
	case *controlpb.Request_Claim, *controlpb.Request_Validate, *controlpb.Request_Start,
		*controlpb.Request_Stop, *controlpb.Request_SetSystemProxy:
		if !d.hostMethods {
			return failure(CodeUnsupportedMethod, methodName(request)+" is only served by the Windows service")
		}
		return failure(CodeNotImplemented, methodName(request)+" is not implemented yet")
	default:
		return failure(CodeNotImplemented, methodName(request)+" is not implemented yet")
	}
}

func (d *Dispatcher) info() *controlpb.GetInfoResponse {
	ownership := controlpb.Ownership_OWNERSHIP_UNSPECIFIED
	if d.hostMethods {
		ownership = controlpb.Ownership_OWNERSHIP_AVAILABLE
	}
	return &controlpb.GetInfoResponse{
		ProtocolVersion: product.ProtocolVersion,
		ServiceVersion:  product.Version,
		CoreVersion:     d.engine.CoreVersion(),
		State:           d.engine.State(),
		Ownership:       ownership,
	}
}

func methodName(request *controlpb.Request) string {
	message := request.ProtoReflect()
	field := message.WhichOneof(message.Descriptor().Oneofs().ByName("method"))
	if field == nil {
		return "request"
	}
	return string(field.Name())
}

func failure(code string, message string) *controlpb.Response {
	return &controlpb.Response{Result: &controlpb.Response_Error{Error: &controlpb.Error{
		Code:    code,
		Message: strings.ToValidUTF8(message, "�"),
	}}}
}

// EncodeFailure builds an encoded Error response for hosts that reject a request
// before it reaches Handle.
func EncodeFailure(id uint64, code string, message string) []byte {
	return encode(id, failure(code, message))
}

func encode(id uint64, response *controlpb.Response) []byte {
	response.ProtocolVersion = product.ProtocolVersion
	response.Id = id
	payload, err := proto.Marshal(response)
	if err != nil {
		payload, _ = proto.Marshal(&controlpb.Response{
			ProtocolVersion: product.ProtocolVersion,
			Id:              id,
			Result:          &controlpb.Response_Error{Error: &controlpb.Error{Code: codeInternal}},
		})
	}
	return payload
}
