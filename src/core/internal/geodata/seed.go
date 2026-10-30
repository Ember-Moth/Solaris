package geodata

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
)

// Files are the GEO data files the installers ship, under the names mihomo looks for in
// its home (docs/routing.md §8.4).
var Files = []string{"GeoSite.dat", "GeoIP.dat"}

// Seed copies each of Files from dir into home when home has none, so that mihomo does
// not download it the first time a rule needs it. A file already in home stays: the
// engine's GEO updates replace it there.
func Seed(dir, home string) error {
	if err := os.MkdirAll(home, 0o700); err != nil {
		return err
	}
	for _, name := range Files {
		target := filepath.Join(home, name)
		if _, err := os.Stat(target); err == nil {
			continue
		} else if !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		if err := copyFile(filepath.Join(dir, name), target); err != nil {
			return fmt.Errorf("copy the bundled %s: %w", name, err)
		}
	}
	return nil
}

// copyFile writes target through a temporary file beside it, so that an interrupted copy
// never leaves a partial file that a later Seed would take for the data. Made in home, the
// copy also takes the home's ACL on Windows, which a file moved in from elsewhere would not.
func copyFile(source, target string) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.CreateTemp(filepath.Dir(target), "."+filepath.Base(target)+"-*")
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	if closeErr := out.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		err = os.Rename(out.Name(), target)
	}
	if err != nil {
		_ = os.Remove(out.Name())
	}
	return err
}
