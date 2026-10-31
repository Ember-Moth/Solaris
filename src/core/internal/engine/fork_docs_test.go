package engine

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"github.com/metacubex/mihomo/common/yaml"
	"github.com/metacubex/mihomo/constant/features"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
)

// Keep the downloadable user templates valid as the fork evolves. Native-tag CI
// covers Naive too; ordinary Go development builds intentionally don't include it.
func TestForkDocumentedProfiles(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "..", "..", "..", "docs", "examples", "mihomo-fork", "*.yaml"))
	if err != nil || len(files) == 0 {
		t.Fatalf("documentation examples: %v (%d files)", err, len(files))
	}
	for _, path := range files {
		t.Run(filepath.Base(path), func(t *testing.T) {
			source, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var inventory struct {
				Proxies []struct {
					Name string `yaml:"name"`
					Type string `yaml:"type"`
				} `yaml:"proxies"`
			}
			if err := yaml.Unmarshal(source, &inventory); err != nil || len(inventory.Proxies) == 0 {
				t.Fatalf("example must contain valid YAML and a node: %v", err)
			}
			for _, node := range inventory.Proxies {
				if node.Type == "naive" && !features.WithNaive {
					t.Skip("Naive examples are validated by the native-tag CI build")
				}
			}
			// Templates contain no usable credentials. Substitute a parser-only key
			// fixture and a loopback address; no connection is dialed by this test.
			source = bytes.ReplaceAll(source, []byte("CHANGE_ME_REALITY_PUBLIC_KEY"), []byte("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"))
			source = bytes.ReplaceAll(source, []byte("proxy.example.com"), []byte("127.0.0.1"))
			admitted, err := admitProfile(admission.Profile{Config: source}, noTUN)
			if err != nil {
				t.Fatal(err)
			}
			for _, proxy := range admitted.cfg.Proxies {
				t.Cleanup(func() { _ = proxy.Close() })
			}
			for _, node := range inventory.Proxies {
				if admitted.cfg.Proxies[node.Name] == nil {
					t.Errorf("documented node %q was lost", node.Name)
				}
			}
			if admitted.cfg.Proxies["PROXY"] == nil || len(admitted.cfg.Rules) == 0 {
				t.Error("example is missing its selector or routing rules")
			}
		})
	}
}
