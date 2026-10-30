package engine

import (
	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/tunnel/statistic"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
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
