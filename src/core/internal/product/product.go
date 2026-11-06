// Package product holds the identifiers that carry the product name, Solaris
// (docs/decisions.md D1); src/solaris/product.py mirrors these values.
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

// WindowsSigner and WindowsSignerEKU name the Authenticode signer that a service linked with
// SignedRelease requires of the UI (docs/architecture.md §5.3): the display name of the
// certificate's subject, the organization Artifact Signing verified (decision D42), and an
// extended key usage that Artifact Signing puts only in the certificates of Solaris's
// certificate profile. src/solaris/update/signature.py and package/windows/sign.ps1 hold the
// same values.
const (
	WindowsSigner    = "Herta Network LLC"
	WindowsSignerEKU = "1.3.6.1.4.1.311.97.242074350.326480862.720558710.91084369"
)

// Version is set at link time with -ldflags "-X <module>/internal/product.Version=<version>".
var Version = "dev"

// SignedRelease is set to "true" at link time, like Version, in the release workflow's
// builds, which it signs. The Windows service then accepts only a UI that WindowsSigner
// signed (docs/architecture.md §5.3). Unsigned builds, such as CI's and the test VM's, leave
// it off.
var SignedRelease = "false"
