//go:build darwin || linux

package peer

import (
	"errors"
	"fmt"
	"net"
	"strconv"
)

// UnixSocketVerifier backs the development socket (docs/architecture.md §11): it accepts
// any process running as allowedUID. Release builds never listen on a Unix socket.
func UnixSocketVerifier(allowedUID uint32) Verifier {
	return func(conn net.Conn) (Identity, error) {
		unixConn, ok := conn.(*net.UnixConn)
		if !ok {
			return Identity{}, errors.New("control connection is not a Unix socket")
		}
		raw, err := unixConn.SyscallConn()
		if err != nil {
			return Identity{}, err
		}
		var uid, pid uint32
		var credentialsErr error
		if err := raw.Control(func(fd uintptr) {
			uid, pid, credentialsErr = peerCredentials(int(fd))
		}); err != nil {
			return Identity{}, err
		}
		if credentialsErr != nil {
			return Identity{}, fmt.Errorf("read socket peer credentials: %w", credentialsErr)
		}
		if uid != allowedUID {
			return Identity{}, fmt.Errorf("socket peer uid %d is not %d", uid, allowedUID)
		}
		return Identity{UserID: strconv.FormatUint(uint64(uid), 10), ProcessID: pid}, nil
	}
}
