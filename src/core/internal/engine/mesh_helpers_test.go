package engine

import (
	"net/netip"
	"testing"

	ZT "github.com/metacubex/zerotier-go"
)

func mustPrefix(t *testing.T, text string) netip.Prefix {
	t.Helper()
	prefix, err := netip.ParsePrefix(text)
	if err != nil {
		t.Fatal(err)
	}
	return prefix
}

// zeroTierPeers are a root, a device with a path and one without.
func zeroTierPeers() []ZT.PeerStatus {
	return []ZT.PeerStatus{
		{Role: ZT.PeerRolePlanet, Paths: []ZT.PeerPath{{}}},
		{Role: ZT.PeerRoleLeaf, Paths: []ZT.PeerPath{{}}},
		{Role: ZT.PeerRoleLeaf},
	}
}
