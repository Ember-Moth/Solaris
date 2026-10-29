package peer

import "golang.org/x/sys/unix"

func peerCredentials(fd int) (uid uint32, pid uint32, err error) {
	credentials, err := unix.GetsockoptUcred(fd, unix.SOL_SOCKET, unix.SO_PEERCRED)
	if err != nil {
		return 0, 0, err
	}
	return credentials.Uid, uint32(credentials.Pid), nil
}
