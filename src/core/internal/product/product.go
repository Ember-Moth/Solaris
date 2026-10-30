// Package product holds the identifiers that carry the product name, Solaris
// (docs/roadmap.md, decision D1); src/solaris/product.py mirrors these values.
package product

const (
	// DisplayName also names the data directories (docs/architecture.md §5.1).
	DisplayName = "Solaris"

	ServiceName = "SolarisService"

	// UIExecutable is the only client the Windows service accepts, from its own directory.
	UIExecutable = "Solaris.exe"

	PipeName = `\\.\pipe\ProtectedPrefix\Administrators\solaris`
	// SYSTEM and Administrators get full access; interactive users may read and write.
	PipeSDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)"

	ProtocolVersion = 1
)

// FrameMagic opens every frame on the Windows pipe.
var FrameMagic = [4]byte{'S', 'L', 'R', 'S'}

// Version is set at link time with -ldflags "-X <module>/internal/product.Version=<version>".
var Version = "dev"
