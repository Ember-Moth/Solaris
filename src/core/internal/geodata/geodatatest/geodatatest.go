// Package geodatatest writes small GEO data files, so tests that compose GEOSITE and GEOIP
// rules never make mihomo download the real ones.
package geodatatest

import (
	"net/netip"
	"os"
	"path/filepath"

	"github.com/metacubex/mihomo/component/geodata/router"
	"google.golang.org/protobuf/proto"
)

// Sites and IPs are the categories Write puts in the files. mihomo checks the files with
// "cn", and the base section's DNS reads "cn" and "private".
var (
	Sites = map[string][]string{
		"cn":       {"example.cn", "baidu.com"},
		"private":  {"localhost.localdomain"},
		"telegram": {"telegram.org", "t.me"},
		"openai":   {"openai.com", "chatgpt.com"},
	}
	IPs = map[string][]string{
		"cn":       {"1.0.1.0/24"},
		"private":  {"10.0.0.0/8", "192.168.0.0/16"},
		"telegram": {"91.108.4.0/22"},
	}
)

// Write writes GeoSite.dat and GeoIP.dat into dir, a mihomo home.
func Write(dir string) error {
	sites := &router.GeoSiteList{}
	for code, domains := range Sites {
		site := &router.GeoSite{CountryCode: code}
		for _, domain := range domains {
			site.Domain = append(site.Domain, &router.Domain{Type: router.Domain_Domain, Value: domain})
		}
		sites.Entry = append(sites.Entry, site)
	}
	ips := &router.GeoIPList{}
	for code, prefixes := range IPs {
		entry := &router.GeoIP{CountryCode: code}
		for _, text := range prefixes {
			prefix := netip.MustParsePrefix(text)
			entry.Cidr = append(entry.Cidr, &router.CIDR{Ip: prefix.Addr().AsSlice(), Prefix: uint32(prefix.Bits())})
		}
		ips.Entry = append(ips.Entry, entry)
	}
	for name, list := range map[string]proto.Message{"GeoSite.dat": sites, "GeoIP.dat": ips} {
		data, err := proto.Marshal(list)
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o644); err != nil {
			return err
		}
	}
	return nil
}
