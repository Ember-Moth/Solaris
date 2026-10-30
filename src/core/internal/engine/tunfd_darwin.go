package engine

import "golang.org/x/sys/unix"

// tunName is the utun interface a kernel control socket is attached to.
func tunName(fd int) (string, error) {
	// SYSPROTO_CONTROL and UTUN_OPT_IFNAME from <sys/sys_domain.h> and <net/if_utun.h>.
	return unix.GetsockoptString(fd, 2, 2)
}
