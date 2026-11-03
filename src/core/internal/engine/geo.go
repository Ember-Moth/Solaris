package engine

import (
	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata"
)

// ListGeoCategories reads the categories of the GEO data in the home directory, the
// files GEOSITE and GEOIP rules use.
func (e *Engine) ListGeoCategories() (*controlpb.ListGeoCategoriesResponse, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	sites, err := geodata.Categories(C.Path.GeoSite())
	if err != nil {
		return nil, err
	}
	ips, err := geodata.Categories(C.Path.GeoIP())
	if err != nil {
		return nil, err
	}
	return &controlpb.ListGeoCategoriesResponse{Geosite: sites, Geoip: ips}, nil
}

// categories are the GEO data's categories, or nil lists, which check nothing, for a kind
// the home has no data of.
func (e *Engine) categories() compose.Categories {
	var categories compose.Categories
	if sites, err := geodata.Categories(C.Path.GeoSite()); err == nil && len(sites.Names) != 0 {
		categories.Geosite = sites.Names
	}
	if ips, err := geodata.Categories(C.Path.GeoIP()); err == nil && len(ips.Names) != 0 {
		categories.Geoip = ips.Names
	}
	return categories
}
