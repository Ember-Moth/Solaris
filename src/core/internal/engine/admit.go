package engine

import (
	"fmt"

	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/hub/executor"
	"github.com/metacubex/mihomo/log"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// admittedConfig is a configuration that passed the admission pipeline.
type admittedConfig struct {
	cfg *config.Config
	// doc is the configuration as it went to mihomo's parser.
	doc map[string]any
	// report and entries come with a composition, and so do the ids of its mesh networks,
	// whose state stays in the home; networks is nil otherwise.
	networks []string
	report   *controlpb.CompositionReport
	entries  *ruleEntries
}

// admit runs a profile through the admission pipeline on this engine's host.
func (e *Engine) admit(profile admission.Profile) (*admittedConfig, error) {
	homeInUse = e.home
	return admitProfile(profile, admission.Host{Tun: e.tunMode, IncludeAllNetworks: e.includeAllNetworks})
}

// admitProfile runs the configuration admission pipeline (docs/architecture.md §3.2):
// admission.Prepare up to the YAML for mihomo → executor.ParseWithBytes → admission.Verify
// on the parsed result. Parsing is the engine's because it touches mihomo's global state.
func admitProfile(profile admission.Profile, host admission.Host) (*admittedConfig, error) {
	prepared, err := admission.Prepare(profile, host)
	if err != nil {
		return nil, err
	}
	for _, warning := range prepared.Warnings {
		log.Warnln("[engine] %s", warning)
	}
	cfg, err := executor.ParseWithBytes(prepared.YAML)
	if err != nil {
		return nil, invalidConfig(err)
	}
	if err := admission.Verify(cfg, prepared); err != nil {
		return nil, err
	}
	admitted := &admittedConfig{cfg: cfg, doc: prepared.Document, networks: prepared.Networks}
	if composed := prepared.Composed; composed != nil {
		admitted.report = composed.Report
		admitted.entries = newRuleEntries(composed, prepared.Document, cfg)
	}
	return admitted, nil
}

func invalidConfig(err error) error {
	return fmt.Errorf("%w: %w", ErrInvalidConfig, err)
}
