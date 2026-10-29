package peer

import "golang.org/x/sys/unix"

func peerCredentials(fd int) (uid uint32, pid uint32, err error) {
	credentials, err := unix.GetsockoptXucred(fd, unix.SOL_LOCAL, unix.LOCAL_PEERCRED)
	if err != nil {
		return 0, 0, err
	}
	processID, err := unix.GetsockoptInt(fd, unix.SOL_LOCAL, unix.LOCAL_PEERPID)
	if err != nil {
		return 0, 0, err
	}
	return credentials.Uid, uint32(processID), nil
}
