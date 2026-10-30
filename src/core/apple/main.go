// Command apple builds the C archive linked into the macOS system extension
// (docs/architecture.md §6.3). handleAppMessage passes each provider message to
// solaris_handle_message and returns the encoded Response to the app.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"context"
	"unsafe"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
	"github.com/Ember-Moth/Solaris/src/core/internal/peer"
)

// dispatcher stays nil until the extension starts the engine in fd mode (M5), and
// solaris_handle_message answers not_implemented meanwhile.
var dispatcher *control.Dispatcher

// solaris_handle_message answers one Request. The returned buffer belongs to the
// caller, who releases it with solaris_free.
//
//export solaris_handle_message
func solaris_handle_message(request unsafe.Pointer, requestLength C.size_t, responseLength *C.size_t) unsafe.Pointer {
	var response []byte
	switch {
	case requestLength > frame.MaxRequestBytes:
		response = control.EncodeFailure(0, control.CodeInvalidRequest, "request exceeds the size limit")
	case dispatcher == nil:
		response = control.EncodeFailure(0, control.CodeNotImplemented, "the extension does not start the engine yet")
	default:
		// The system restricts provider messages to this app, so there is no peer identity.
		response = dispatcher.Handle(context.Background(), peer.Identity{}, C.GoBytes(request, C.int(requestLength)))
	}
	*responseLength = C.size_t(len(response))
	return C.CBytes(response)
}

//export solaris_free
func solaris_free(buffer unsafe.Pointer) {
	C.free(buffer)
}

func main() {}
