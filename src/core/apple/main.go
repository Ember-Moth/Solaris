// Command apple builds the C archive linked into the macOS system extension
// (docs/architecture.md §6). The extension reads the tunnel's network with
// solaris_tunnel_network, attaches the utun descriptor with solaris_attach_tun once its
// network settings are in place, hands each provider message to solaris_handle_message,
// and detaches with solaris_detach_tun before the tunnel goes down.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"unsafe"

	"github.com/Ember-Moth/Solaris/src/core/internal/control"
	"github.com/Ember-Moth/Solaris/src/core/internal/frame"
)

// solaris_tunnel_network describes the tunnel's network (engine.FDNetwork) as JSON for
// the extension's network settings. The caller releases the buffer with solaris_free.
//
//export solaris_tunnel_network
func solaris_tunnel_network(length *C.size_t) unsafe.Pointer {
	data := networkJSON()
	*length = C.size_t(len(data))
	return C.CBytes(data)
}

// solaris_attach_tun opens the engine in fd mode on the utun descriptor, with home as
// mihomo's home directory, and the dispatcher that answers solaris_handle_message; the
// core runs once a Start message brings its configuration. Before that, it copies the GEO
// data in resources, the extension's resource directory, into home where home lacks it.
// The descriptor stays the caller's: Go never closes it, and mihomo runs on duplicates of
// it that Go closes. It returns NULL, or an error the caller releases with solaris_free.
//
//export solaris_attach_tun
func solaris_attach_tun(fd C.int, home *C.char, resources *C.char) *C.char {
	if err := attach(int(fd), C.GoString(home), C.GoString(resources)); err != nil {
		return C.CString(err.Error())
	}
	return nil
}

// solaris_detach_tun stops the core and closes the dispatcher and every duplicate of the
// descriptor. Once it returns, Go no longer uses the descriptor, and the caller may close it.
//
//export solaris_detach_tun
func solaris_detach_tun() {
	detach()
}

// solaris_handle_message answers one Request. The returned buffer belongs to the
// caller, who releases it with solaris_free.
//
//export solaris_handle_message
func solaris_handle_message(request unsafe.Pointer, requestLength C.size_t, responseLength *C.size_t) unsafe.Pointer {
	var response []byte
	if requestLength > frame.MaxRequestBytes {
		response = control.EncodeFailure(0, control.CodeInvalidRequest, "request exceeds the size limit")
	} else {
		response = handle(C.GoBytes(request, C.int(requestLength)))
	}
	*responseLength = C.size_t(len(response))
	return C.CBytes(response)
}

//export solaris_free
func solaris_free(buffer unsafe.Pointer) {
	C.free(buffer)
}

func main() {}
