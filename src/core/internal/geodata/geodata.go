// Package geodata reads the category names out of mihomo's GEO data files (docs/routing.md
// §8.4), the same files GEOSITE and GEOIP rules match with.
package geodata

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"slices"
	"strings"

	"google.golang.org/protobuf/encoding/protowire"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Both GeoSiteList and GeoIPList hold their entries in field 1, and each entry its
// category in field 1 (mihomo's component/geodata/router/config.proto).
const (
	entryField    protowire.Number = 1
	categoryField protowire.Number = 1
)

// Categories lists the categories in the GeoSite.dat or GeoIP.dat at path, lower-cased
// and sorted as rules write them. A missing file has none.
func Categories(path string) (*controlpb.GeoCategories, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return &controlpb.GeoCategories{}, nil
	}
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	names, err := categoryNames(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	digest := sha256.Sum256(data)
	return &controlpb.GeoCategories{
		Names:           names,
		Sha256:          hex.EncodeToString(digest[:]),
		UpdatedAtUnixMs: uint64(info.ModTime().UnixMilli()),
	}, nil
}

// categoryNames walks the list without decoding the domains and IP ranges.
func categoryNames(data []byte) ([]string, error) {
	var names []string
	for len(data) > 0 {
		number, kind, n := protowire.ConsumeTag(data)
		if n < 0 {
			return nil, protowire.ParseError(n)
		}
		data = data[n:]
		if number != entryField || kind != protowire.BytesType {
			if n = protowire.ConsumeFieldValue(number, kind, data); n < 0 {
				return nil, protowire.ParseError(n)
			}
			data = data[n:]
			continue
		}
		entry, n := protowire.ConsumeBytes(data)
		if n < 0 {
			return nil, protowire.ParseError(n)
		}
		data = data[n:]
		name, err := category(entry)
		if err != nil {
			return nil, err
		}
		if name != "" {
			names = append(names, strings.ToLower(name))
		}
	}
	slices.Sort(names)
	return slices.Compact(names), nil
}

func category(entry []byte) (string, error) {
	for len(entry) > 0 {
		number, kind, n := protowire.ConsumeTag(entry)
		if n < 0 {
			return "", protowire.ParseError(n)
		}
		entry = entry[n:]
		if number == categoryField && kind == protowire.BytesType {
			value, n := protowire.ConsumeBytes(entry)
			if n < 0 {
				return "", protowire.ParseError(n)
			}
			return string(value), nil
		}
		if n = protowire.ConsumeFieldValue(number, kind, entry); n < 0 {
			return "", protowire.ParseError(n)
		}
		entry = entry[n:]
	}
	return "", nil
}
