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
}

// Verifier accepts or rejects a freshly accepted connection.
type Verifier func(net.Conn) (Identity, error)
