// Command service is the LocalSystem service on Windows (docs/architecture.md §5),
// built as mihomo-desktop-service.exe with go build -o. On macOS and Linux it only
// runs as a development server on a Unix socket (§11); the macOS release uses the
// system extension instead.
package main

import (
	"fmt"
	"os"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/control"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/engine"
)

func newDispatcher() *control.Dispatcher {
	return control.New(control.Options{Engine: engine.Unlinked{}, HostMethods: true})
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
