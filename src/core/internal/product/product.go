// Package product holds the identifiers that change with the product name.
//
// "mihomo-desktop" is a working name until the product is named
// (docs/architecture.md §12); src/mihomo_desktop/product.py mirrors these values.
package product

const (
	// DisplayName also names the data directories (docs/architecture.md §5.1).
	DisplayName = "Mihomo Desktop"

	ServiceName = "MihomoDesktopService"

	// UIExecutable is the only client the Windows service accepts, from its own directory.
	UIExecutable = "mihomo-desktop.exe"

	PipeName = `\\.\pipe\ProtectedPrefix\Administrators\mihomo-desktop`
	// SYSTEM and Administrators get full access; interactive users may read and write.
	PipeSDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"

	ProtocolVersion = 1
)

// FrameMagic opens every frame on the Windows pipe.
var FrameMagic = [4]byte{'M', 'D', 'C', 'P'}

// Version is set at link time with -ldflags "-X <module>/internal/product.Version=<version>".
var Version = "dev"
