package engine

import (
	"errors"
	"io/fs"
	"os"
	"time"

	"github.com/metacubex/mihomo/component/updater"
	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/log"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata"
)

// ListGeoCategories reads the categories of the GEO data in the home directory, the
// files GEOSITE and GEOIP rules use.
func (e *Engine) ListGeoCategories() (*controlpb.ListGeoCategoriesResponse, error) {
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

// geoCheckInterval is how often the updater looks at the age of the GEO data.
const geoCheckInterval = time.Hour

// geoUpdater refreshes the GEO data while a configuration with geo-auto-update runs:
// mihomo only schedules its own updater from its main, which the embedded core never
// runs. Fresh data takes effect with the next Start or Reload.
type geoUpdater struct {
	interval time.Duration // the configuration's geo-update-interval
	stop     chan struct{}
	done     chan struct{}
}

// syncGeoUpdater runs the updater when cfg asks for it, restarting it when the interval
// changes, and stops it otherwise. The lifecycle lock holds.
func (e *Engine) syncGeoUpdater(cfg *config.Config) {
	var interval time.Duration
	if cfg != nil && cfg.General.GeoAutoUpdate {
		interval = time.Duration(cfg.General.GeoUpdateInterval) * time.Hour
	}
	if e.geo != nil && e.geo.interval == interval {
		return
	}
	if e.geo != nil {
		close(e.geo.stop)
		<-e.geo.done
		e.geo = nil
	}
	if interval > 0 {
		// mihomo's path lookups record the file names they find in package state that
		// parsing a configuration shares, so they run here and not in the updater.
		paths := []string{C.Path.GeoSite(), C.Path.GeoIP()}
		e.geo = &geoUpdater{interval: interval, stop: make(chan struct{}), done: make(chan struct{})}
		go e.geo.run(geoCheckInterval, func() time.Time { return geoDataTime(paths) }, updater.UpdateGeoDatabases)
	}
}

func (g *geoUpdater) run(check time.Duration, dataTime func() time.Time, update func() error) {
	defer close(g.done)
	ticker := time.NewTicker(check)
	defer ticker.Stop()
	for {
		if geoUpdateDue(dataTime(), g.interval, time.Now()) {
			if err := update(); err != nil {
				log.Warnln("[engine] GEO data update: %s", err)
			}
		}
		select {
		case <-g.stop:
			return
		case <-ticker.C:
		}
	}
}

// geoUpdateDue is true once the data is an interval old. Without data there is nothing to
// refresh: mihomo downloads it when a rule first needs it.
func geoUpdateDue(dataTime time.Time, interval time.Duration, now time.Time) bool {
	return !dataTime.IsZero() && interval > 0 && !now.Before(dataTime.Add(interval))
}

// geoDataTime is when the oldest of the GEO data files at paths was written.
func geoDataTime(paths []string) time.Time {
	var oldest time.Time
	for _, path := range paths {
		info, err := os.Stat(path)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			log.Warnln("[engine] GEO data: %s", err)
			continue
		}
		if oldest.IsZero() || info.ModTime().Before(oldest) {
			oldest = info.ModTime()
		}
	}
	return oldest
}
