// Command apple builds the C archive linked into the macOS system extension
// (docs/architecture.md §6.3). handleAppMessage passes each provider message to
// mihomo_desktop_handle_message and returns the encoded Response to the app.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"context"
	"unsafe"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/control"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/engine"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/frame"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
)

var dispatcher = control.New(control.Options{Engine: engine.Unlinked{}})

// mihomo_desktop_handle_message answers one Request. The returned buffer belongs to the
// caller, who releases it with mihomo_desktop_free.
//
//export mihomo_desktop_handle_message
func mihomo_desktop_handle_message(request unsafe.Pointer, requestLength C.size_t, responseLength *C.size_t) unsafe.Pointer {
	var response []byte
	if requestLength > frame.MaxRequestBytes {
		response = control.EncodeFailure(0, control.CodeInvalidRequest, "request exceeds the size limit")
	} else {
		// The system restricts provider messages to this app, so there is no peer identity.
		response = dispatcher.Handle(context.Background(), peer.Identity{}, C.GoBytes(request, C.int(requestLength)))
	}
	*responseLength = C.size_t(len(response))
	return C.CBytes(response)
}

//export mihomo_desktop_free
func mihomo_desktop_free(buffer unsafe.Pointer) {
	C.free(buffer)
}

func main() {}
