// Command service is the LocalSystem service on Windows (docs/architecture.md §5),
// built as solaris-service.exe with go build -o. On macOS and Linux it only
// runs as a development server on a Unix socket (§11); the macOS release uses the
// system extension instead.
package main

import (
	"fmt"
	"os"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
