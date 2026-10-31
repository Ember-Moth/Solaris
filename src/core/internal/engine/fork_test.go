package engine

import (
	"bytes"
	"testing"

	"github.com/metacubex/mihomo/common/yaml"
	"github.com/metacubex/mihomo/config"
	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
)

func forkNode(cfg *config.Config) C.Proxy {
	if proxy := cfg.Proxies["node"]; proxy != nil {
		return proxy
	}
	// Composed sources are inline providers, rather than top-level proxies.
	for _, provider := range cfg.Providers {
		for _, proxy := range provider.Proxies() {
			if proxy.Name() == "node" {
				return proxy
			}
		}
	}
	return nil
}

func forkProfile(t *testing.T, node map[string]any, composed bool) admission.Profile {
	t.Helper()
	source, err := yaml.Marshal(map[string]any{"proxies": []any{node}, "rules": []string{"MATCH,node"}})
	if err != nil {
		t.Fatal(err)
	}
	if !composed {
		return admission.Profile{Config: source}
	}
	return admission.Profile{Composition: &compose.Composition{
		Sources: []compose.Source{{ID: "fork", Name: "fork", YAML: source}},
		Plan:    []byte(`{"version":1}`),
	}}
}

func TestForkAnyTLSRealitySurvivesAdmissionAndComposition(t *testing.T) {
	for _, composed := range []bool{false, true} {
		node := map[string]any{
			"name": "node", "type": "anytls", "server": "127.0.0.1", "port": 443,
			"password": "test", "sni": "reality.example", "client-fingerprint": "chrome",
			"reality-opts": map[string]any{
				"public-key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
				"short-id":   "0123456789abcdef", "support-x25519mlkem768": false,
			},
		}
		admitted, err := admitProfile(forkProfile(t, node, composed), noTUN)
		if err != nil {
			t.Fatal(err)
		}
		proxy := forkNode(admitted.cfg)
		if proxy == nil || proxy.Type() != C.AnyTLS {
			t.Fatal("AnyTLS was not retained")
		}
		_ = proxy.Close()
		raw, err := yaml.Marshal(admitted.doc)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Contains(raw, []byte("support-x25519mlkem768: false")) {
			t.Fatalf("explicit REALITY legacy choice was lost: %s", raw)
		}
		// The fork must reject an incompatible security configuration, not ignore it.
		node["ech-opts"] = map[string]any{"enable": true}
		if _, err := admitProfile(forkProfile(t, node, composed), noTUN); err == nil {
			t.Fatal("accepted ECH together with REALITY")
		}
	}
}
