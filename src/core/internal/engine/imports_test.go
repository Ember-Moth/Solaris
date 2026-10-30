package engine

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// mihomoRuntime are mihomo's packages that hold its runtime in package-level state or act
// on it. Only this package imports them (docs/architecture.md §3.2). listener/config and
// listener/inbound are types, so only the listener package itself is here.
var mihomoRuntime = []string{
	"hub",
	"hub/executor",
	"hub/route",
	"tunnel",
	"tunnel/statistic",
	"listener",
	"component/resolver",
	"component/updater",
	"component/profile/cachefile",
	"component/proxydialer",
	"log",
}

// Every other package of the module leaves mihomo's runtime to the engine: none of their
// files but tests, whatever the build tags, imports a package of mihomoRuntime. Test
// helper packages such as geodatatest are packages like any other and are held to it too.
func TestOnlyTheEngineTouchesMihomosRuntime(t *testing.T) {
	denied := map[string]bool{}
	for _, path := range mihomoRuntime {
		denied[mihomoModule+"/"+path] = true
	}
	engine := filepath.Join("..", "engine")
	scanned := map[string]bool{}
	for _, root := range []string{"..", filepath.Join("..", "..", "service"), filepath.Join("..", "..", "apple")} {
		err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() {
				name := entry.Name()
				if path == engine || path != root && (name == "testdata" || strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_")) {
					return filepath.SkipDir
				}
				return nil
			}
			if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
				return nil
			}
			file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
			if err != nil {
				return err
			}
			scanned[filepath.Dir(path)] = true
			for _, spec := range file.Imports {
				imported, err := strconv.Unquote(spec.Path.Value)
				if err != nil {
					return err
				}
				if denied[imported] {
					t.Errorf("%s imports %s; only internal/engine may", path, imported)
				}
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	// The packages that use mihomo's parsers and types, so the walk cannot miss them quietly.
	for _, dir := range []string{"admission", "compose", "vpngate", filepath.Join("geodata", "geodatatest")} {
		if !scanned[filepath.Join("..", dir)] {
			t.Errorf("internal/%s was not checked", dir)
		}
	}
	for _, dir := range []string{"service", "apple"} {
		if !scanned[filepath.Join("..", "..", dir)] {
			t.Errorf("%s was not checked", dir)
		}
	}
}
