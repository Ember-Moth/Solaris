package admission

import (
	"fmt"
	"slices"

	"github.com/metacubex/mihomo/common/yaml"
)

// listInsert is an override key that adds to a list instead of replacing it.
type listInsert struct {
	key, target string
	prepend     bool
}

var listInserts = []listInsert{
	{"prepend-rules", "rules", true},
	{"append-rules", "rules", false},
	{"prepend-proxies", "proxies", true},
	{"append-proxies", "proxies", false},
	{"prepend-proxy-groups", "proxy-groups", true},
	{"append-proxy-groups", "proxy-groups", false},
}

// applyOverrides merges each override into the configuration in order. Mappings merge
// key by key; scalars and lists replace what was there; the prepend-* and append-* keys
// put their lists before or after rules, proxies and proxy-groups. They run before the
// platform patches and the security items, so they cannot undo either.
func applyOverrides(doc map[string]any, overrides [][]byte) error {
	for i, override := range overrides {
		if err := applyOverride(doc, override); err != nil {
			return fmt.Errorf("override %d of %d: %w", i+1, len(overrides), err)
		}
	}
	return nil
}

func applyOverride(doc map[string]any, source []byte) error {
	var patch map[string]any
	if err := yaml.Unmarshal(source, &patch); err != nil {
		return err
	}
	inserts := map[string][]any{}
	for key, value := range patch {
		if !isListInsert(key) {
			mergeValue(doc, key, value)
			continue
		}
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s must be a list", key)
		}
		inserts[key] = items
	}
	// Inserts land after this override's own replacements of the same list.
	for _, insert := range listInserts {
		items, ok := inserts[insert.key]
		if !ok {
			continue
		}
		var existing []any
		switch current := doc[insert.target].(type) {
		case nil:
		case []any:
			existing = current
		default:
			return fmt.Errorf("%s cannot extend %s, which is not a list", insert.key, insert.target)
		}
		if insert.prepend {
			doc[insert.target] = append(slices.Clone(items), existing...)
		} else {
			doc[insert.target] = append(slices.Clone(existing), items...)
		}
	}
	return nil
}

func isListInsert(key string) bool {
	return slices.ContainsFunc(listInserts, func(insert listInsert) bool { return insert.key == key })
}

func mergeValue(target map[string]any, key string, value any) {
	if nested, ok := value.(map[string]any); ok {
		if existing, ok := target[key].(map[string]any); ok {
			for nestedKey, nestedValue := range nested {
				mergeValue(existing, nestedKey, nestedValue)
			}
			return
		}
	}
	target[key] = value
}
