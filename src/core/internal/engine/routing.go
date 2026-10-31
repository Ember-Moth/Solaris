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
	"github.com/metacubex/mihomo/tunnel/statistic"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
	"github.com/Ember-Moth/Solaris/src/core/internal/geodata"
)

// ruleEntries ties the rules of a composed configuration to the plan's rule entries, so
// the rule list and the connections can name the entry (docs/routing.md §8.1).
type ruleEntries struct {
	byIndex []string // the entry of each rule, in order; empty for an override's rule
	// byRule finds the entry from what a connection records of its rule: the type, the
	// payload and the target, which is the outermost link of its chain. The first rule
	// with that key is the one mihomo matches.
	byRule map[ruleKey]string
}

type ruleKey struct{ kind, payload, target string }

// newRuleEntries matches the final rules, after the overrides, to the lines the plan
// produced. It gives up when the parsed rules do not line up with the configuration's.
func newRuleEntries(composed *compose.Result, doc map[string]any, cfg *config.Config) *ruleEntries {
	byLine := map[string]string{}
	for i, line := range composed.Lines {
		if _, ok := byLine[line]; !ok {
			byLine[line] = composed.Report.Rules[i].EntryId
		}
	}
	lines, _ := doc["rules"].([]any)
	if len(lines) != len(cfg.Rules) {
		return nil
	}
	entries := &ruleEntries{byIndex: make([]string, len(lines)), byRule: map[ruleKey]string{}}
	for i, raw := range lines {
		line, _ := raw.(string)
		entries.byIndex[i] = byLine[line]
		rule := cfg.Rules[i]
		key := ruleKey{rule.RuleType().String(), rule.Payload(), rule.Adapter()}
		if _, ok := entries.byRule[key]; !ok {
			entries.byRule[key] = byLine[line]
		}
	}
	return entries
}

func (r *ruleEntries) index(i int) string {
	if r == nil || i >= len(r.byIndex) {
		return ""
	}
	return r.byIndex[i]
}

func (r *ruleEntries) connection(info *statistic.TrackerInfo) string {
	if r == nil || info.Rule == "" {
		return ""
	}
	target := ""
	if chain := info.Chain; len(chain) > 0 {
		target = chain[len(chain)-1]
	}
	return r.byRule[ruleKey{info.Rule, info.RulePayload, target}]
}

func (e *Engine) setEntries(entries *ruleEntries) {
	e.mu.Lock()
	e.entries = entries
	e.mu.Unlock()
}

func (e *Engine) ruleEntries() *ruleEntries {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.entries
}

// InspectSource describes a subscription for the UI; it needs no running core.
func (e *Engine) InspectSource(source []byte, infoFilter string) (*controlpb.InspectSourceResponse, error) {
	inspection, err := compose.Inspect(source, infoFilter)
	if err != nil {
		return nil, invalidConfig(err)
	}
	return inspection, nil
}

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
