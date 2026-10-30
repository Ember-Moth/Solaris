package admission

import (
	"fmt"
	"path/filepath"
)

// controllerKeys open mihomo's own controller or files it serves. The pipeline clears
// them last, after the overrides (§3.3 #2); the external UI keys go too because the
// updater would download a dashboard into the home directory for a controller that never runs.
var controllerKeys = []string{
	"external-controller",
	"external-controller-tls",
	"external-controller-unix",
	"external-controller-pipe",
	"external-doh-server",
	"secret",
	"external-ui",
	"external-ui-url",
	"external-ui-name",
}

// enforceSecurity is the last step before parsing, after the overrides, so neither the
// profile nor an override can undo it (docs/architecture.md §3.3). It reports the mesh
// outbound options it turned off.
func enforceSecurity(doc map[string]any) ([]meshRestriction, error) {
	// Empty, not absent: a missing external-ui-url falls back to mihomo's download URL.
	for _, key := range controllerKeys {
		doc[key] = ""
	}
	// Node selections survive a restart and the next boot (§3.3 #5).
	section(doc, "profile")["store-selected"] = true
	// iptables is Linux-only and mihomo exits the process when it cannot apply it.
	doc["iptables"] = map[string]any{"enable": false}
	// Setting the clock needs a privilege the user may not hold; SYSTEM must not do it for them.
	section(doc, "ntp")["write-to-system"] = false
	for _, key := range []string{"proxy-providers", "rule-providers"} {
		if err := checkProviderPaths(doc, key); err != nil {
			return nil, err
		}
	}
	return restrictMesh(doc)
}

// checkProviderPaths keeps provider files inside the home directory with the
// platform's own path rules, so `..\` cannot climb out on Windows (§3.3 #4). mihomo's
// IsSafePath stays as the backstop.
func checkProviderPaths(doc map[string]any, key string) error {
	raw, ok := doc[key]
	if !ok || raw == nil {
		return nil
	}
	providers, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("%s must be a mapping", key)
	}
	for name, value := range providers {
		provider, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s.%s must be a mapping", key, name)
		}
		rawPath, ok := provider["path"]
		if !ok || rawPath == nil {
			continue
		}
		path, ok := rawPath.(string)
		if !ok {
			return fmt.Errorf("%s.%s.path must be a string", key, name)
		}
		if path == "" {
			delete(provider, "path")
			continue
		}
		local, err := localPath(path)
		if err != nil {
			return fmt.Errorf("%s.%s.path: %w", key, name, err)
		}
		provider["path"] = local
	}
	return nil
}

func localPath(path string) (string, error) {
	cleaned := filepath.Clean(filepath.FromSlash(path))
	if !filepath.IsLocal(cleaned) {
		return "", fmt.Errorf("%q must stay inside the home directory", path)
	}
	return cleaned, nil
}
