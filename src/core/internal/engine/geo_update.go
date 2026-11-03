package engine

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	geo "github.com/metacubex/mihomo/component/geodata"
	_ "github.com/metacubex/mihomo/component/geodata/standard"
	"github.com/metacubex/mihomo/component/mmdb"
	C "github.com/metacubex/mihomo/constant"
	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

const geoMaxBytes = 256 << 20
const geoChunkBytes = 48 << 10

type geoTransfer struct {
	id, name, digest, path string
	size, offset           uint64
	touched                time.Time
}

type geoState struct {
	plan   *controlpb.GetGeoUpdateResponse
	paths  map[string]string
	upload *geoTransfer
}

func (g *geoState) abort() {
	if g.upload != nil {
		_ = os.Remove(g.upload.path)
		g.upload = nil
	}
}

func (g *geoState) reset() { g.abort(); g.plan = nil; g.paths = nil }

// Called under lifecycle, after the configuration has actually applied. Validate and
// rejected reloads never replace this snapshot. Counters avoid the fork's sticky flags:
// an ASN used by an earlier profile must not keep making the current profile overdue.
func (e *Engine) syncGeoConfig(admitted *admittedConfig) {
	e.geo.reset()
	g := admitted.cfg.General
	if !g.GeoAutoUpdate || g.GeoUpdateInterval <= 0 {
		return
	}
	var token [16]byte
	if _, err := rand.Read(token[:]); err != nil {
		return
	}
	e.geo.plan = &controlpb.GetGeoUpdateResponse{
		Generation: hex.EncodeToString(token[:]), IntervalHours: uint32(g.GeoUpdateInterval),
	}
	e.geo.paths = make(map[string]string)
	add := func(name, url, path string) {
		e.geo.paths[name] = path
		e.geo.plan.Files = append(e.geo.plan.Files, &controlpb.GeoUpdateFile{Name: name, Url: url})
	}
	used := geo.Usage()
	if used.Site != admitted.geoBefore.Site {
		add("GeoSite.dat", g.GeoXUrl.GeoSite, C.Path.GeoSite())
	}
	if used.IP != admitted.geoBefore.IP {
		if g.GeodataMode {
			add("GeoIP.dat", g.GeoXUrl.GeoIp, C.Path.GeoIP())
		} else {
			add("geoip.metadb", g.GeoXUrl.Mmdb, C.Path.MMDB())
		}
	}
	if used.ASN != admitted.geoBefore.ASN {
		add("ASN.mmdb", g.GeoXUrl.ASN, C.Path.ASN())
	}
}

func (e *Engine) GetGeoUpdate() (*controlpb.GetGeoUpdateResponse, error) {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return nil, err
	}
	if e.geo.upload != nil && time.Since(e.geo.upload.touched) > 5*time.Minute {
		e.geo.abort()
	}
	if e.geo.plan == nil || !e.running() {
		return &controlpb.GetGeoUpdateResponse{}, nil
	}
	result := proto.Clone(e.geo.plan).(*controlpb.GetGeoUpdateResponse)
	for _, file := range result.Files {
		if info, err := os.Stat(e.geo.paths[file.Name]); err == nil {
			file.UpdatedAtUnixMs = info.ModTime().UnixMilli()
		}
	}
	return result, nil
}

func validHex(value string, size int) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == size && value == strings.ToLower(value)
}

// UploadGeo never downloads anything or accepts caller paths. Each request has a small
// bound, and the one in-flight upload belongs to the applied configuration generation.
// Holding lifecycle excludes parser/cache mutation; Stop waits only for a local chunk
// or verification, never for a remote server. Cancellation before persist is checked;
// after a successful rename publication must finish even if the caller has gone away.
func (e *Engine) UploadGeo(ctx context.Context, request *controlpb.UploadGeoRequest) error {
	e.lifecycle.Lock()
	defer e.lifecycle.Unlock()
	if err := e.checkOpen(); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	g := &e.geo
	bad := func(message string) error { return fmt.Errorf("%w: GEO %s", ErrInvalidArgument, message) }
	if g.plan == nil || request.Generation != g.plan.Generation || !e.running() {
		return bad("configuration changed")
	}
	if request.Abort {
		if g.upload != nil && request.TransferId == g.upload.id {
			g.abort()
		}
		return nil
	}
	target, allowed := g.paths[request.Name]
	if !allowed || !validHex(request.TransferId, 16) || !validHex(request.Sha256, 32) ||
		request.Size == 0 || request.Size > geoMaxBytes || len(request.Data) > geoChunkBytes {
		return bad("invalid upload metadata")
	}
	if g.upload != nil && time.Since(g.upload.touched) > 5*time.Minute {
		g.abort()
	}
	if g.upload == nil || g.upload.id != request.TransferId {
		if g.upload != nil {
			return bad("another upload is in progress")
		}
		if request.Offset != 0 || request.Complete {
			return bad("upload must start at offset zero")
		}
		file, err := os.CreateTemp(e.home, ".geo-upload-*")
		if err != nil {
			return err
		}
		if err := file.Close(); err != nil {
			_ = os.Remove(file.Name())
			return err
		}
		g.upload = &geoTransfer{id: request.TransferId, name: request.Name, digest: request.Sha256, path: file.Name(), size: request.Size}
	}
	u := g.upload
	if u.name != request.Name || u.size != request.Size || u.digest != request.Sha256 ||
		u.offset != request.Offset || uint64(len(request.Data)) > u.size-u.offset {
		return bad("upload metadata or offset changed")
	}
	u.touched = time.Now()
	if len(request.Data) != 0 {
		file, err := os.OpenFile(u.path, os.O_WRONLY|os.O_APPEND, 0)
		if err != nil {
			g.abort()
			return err
		}
		n, err := file.Write(request.Data)
		closeErr := file.Close()
		if err == nil {
			err = closeErr
		}
		if err != nil || n != len(request.Data) {
			g.abort()
			return bad("could not write full chunk")
		}
		u.offset += uint64(n)
	}
	if !request.Complete {
		return nil
	}
	defer g.abort()
	if u.offset != u.size {
		return bad("incomplete upload")
	}
	data, err := os.ReadFile(u.path)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(data)
	if hex.EncodeToString(digest[:]) != u.digest {
		return bad("checksum mismatch")
	}
	persist := func() error {
		if err := ctx.Err(); err != nil {
			return err
		}
		// Rename from the same secured directory preserves the service's inherited ACL.
		file, err := os.OpenFile(u.path, os.O_WRONLY, 0)
		if err != nil {
			return err
		}
		err = file.Sync()
		closeErr := file.Close()
		if err == nil {
			err = closeErr
		}
		if err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		return os.Rename(u.path, target)
	}
	switch request.Name {
	case "geoip.metadb":
		return mmdb.InstallIP(data, persist)
	case "ASN.mmdb":
		return mmdb.InstallASN(data, persist)
	default:
		loader, err := geo.GetGeoDataLoader("standard")
		if err != nil {
			return err
		}
		if request.Name == "GeoSite.dat" {
			_, err = loader.LoadSiteByBytes(data, "cn")
		} else {
			_, err = loader.LoadIPByBytes(data, "cn")
		}
		if err != nil {
			return bad("invalid database: " + err.Error())
		}
		if err := persist(); err != nil {
			return err
		}
		if request.Name == "GeoSite.dat" {
			geo.ClearGeoSiteCache()
		} else {
			geo.ClearGeoIPCache()
		}
		return nil
	}
}

// Interrupted transfers are never considered installed data, including after a crash.
func cleanGeoUploads(home string) {
	paths, _ := filepath.Glob(filepath.Join(home, ".geo-upload-*"))
	for _, path := range paths {
		_ = os.Remove(path)
	}
}
