package engine

import "golang.org/x/sys/unix"

// tunName is the TUN interface a /dev/net/tun descriptor is attached to.
func tunName(fd int) (string, error) {
	request, err := unix.NewIfreq("")
	if err != nil {
		return "", err
	}
	if err := unix.IoctlIfreq(fd, unix.TUNGETIFF, request); err != nil {
		return "", err
	}
	return request.Name(), nil
}
