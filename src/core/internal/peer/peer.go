// Package peer identifies the process on the other end of a control connection.
package peer

import "net"

// Identity is the verified caller. On Windows UserID is the token SID; on Unix it is
// the numeric uid. macOS provider messages carry no identity because the system only
// hands the tunnel session to this app.
type Identity struct {
	UserID    string
	SessionID uint32
	ProcessID uint32
	// Administrator says the caller runs with an administrator's rights: on Windows its
	// token has the Administrators group enabled, which UAC leaves out of an administrator's
	// processes until they confirm an elevation (decision D5b). Unix callers never have it.
	Administrator bool
}

// Verifier accepts or rejects a freshly accepted connection. The connection it returns is
// the one to serve and close: it may hold what the check needs kept until then.
type Verifier func(net.Conn) (net.Conn, Identity, error)
