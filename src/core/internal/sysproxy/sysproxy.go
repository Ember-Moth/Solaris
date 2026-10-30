// Package sysproxy reads and writes a user's WinINet proxy settings: the system proxy the
// Windows service points at the core's mixed port for its owner (docs/architecture.md §5.5).
package sysproxy

import (
	"net"
	"strconv"
)

// WinINet's PROXY_TYPE_* bits for Settings.Flags.
const (
	Direct       = 1
	Proxy        = 2
	AutoProxyURL = 4
	AutoDetect   = 8
)

// Settings are the proxy settings of a user's default connection.
type Settings struct {
	Flags         uint32 `json:"flags"`
	Server        string `json:"server"`
	Bypass        string `json:"bypass"`
	AutoConfigURL string `json:"autoconfig_url"`
}

// Bypass keeps loopback and private addresses off the proxy; "<local>" covers plain host
// names.
const Bypass = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;" +
	"172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;" +
	"172.31.*;192.168.*;<local>"

// Core sends a user's traffic to the core's mixed port on port.
func Core(port uint16) Settings {
	return Settings{
		Flags:  Direct | Proxy,
		Server: net.JoinHostPort("127.0.0.1", strconv.Itoa(int(port))),
		Bypass: Bypass,
	}
}
