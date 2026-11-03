package engine

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/geodata/router"
	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/admission"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

func startGeo(t *testing.T) (*Engine, *controlpb.GetGeoUpdateResponse) {
	t.Helper()
	e := openEngine(t, admission.TunNone)
	profile := admission.Profile{Config: []byte("geodata-mode: true\ngeo-auto-update: true\nrules:\n  - GEOSITE,cn,DIRECT\n")}
	if _, err := e.Start(profile); err != nil {
		t.Fatal(err)
	}
	plan, err := e.GetGeoUpdate()
	if err != nil || len(plan.Files) != 1 || plan.Files[0].Name != "GeoSite.dat" {
		t.Fatalf("plan = %v, %v", plan, err)
	}
	return e, plan
}

func uploadRequest(plan *controlpb.GetGeoUpdateResponse, data []byte) *controlpb.UploadGeoRequest {
	digest := sha256.Sum256(data)
	return &controlpb.UploadGeoRequest{Generation: plan.Generation, TransferId: strings.Repeat("a", 32), Name: "GeoSite.dat", Size: uint64(len(data)), Sha256: hex.EncodeToString(digest[:])}
}

func TestGeoUploadChecksBeforeReplacingAndCancels(t *testing.T) {
	e, plan := startGeo(t)
	target := e.geo.paths["GeoSite.dat"]
	before, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.WriteFile(target, before, 0o600) })
	for _, test := range []string{"format", "checksum", "cancelled", "offset", "oversize", "path"} {
		t.Run(test, func(t *testing.T) {
			r := uploadRequest(plan, []byte("bad database"))
			r.Data = []byte("bad database")
			if test == "offset" {
				r.Offset = 1
			}
			if test == "oversize" {
				r.Size = geoMaxBytes + 1
			}
			if test == "path" {
				r.Name = "../GeoSite.dat"
			}
			err := e.UploadGeo(context.Background(), r)
			if test == "offset" || test == "oversize" || test == "path" {
				if err == nil {
					t.Fatal("accepted invalid first chunk")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			r.Offset, r.Data, r.Complete = r.Size, nil, true
			if test == "checksum" {
				_ = os.WriteFile(e.geo.upload.path, []byte("corrupted"), 0o600)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if test == "cancelled" {
				cancel()
			}
			if err := e.UploadGeo(ctx, r); err == nil {
				t.Fatal("accepted incomplete/invalid/cancelled data")
			}
			e.geo.abort()
			got, _ := os.ReadFile(target)
			if !bytes.Equal(before, got) {
				t.Fatal("changed installed data on failure")
			}
		})
	}
	paths, _ := filepath.Glob(filepath.Join(e.home, ".geo-upload-*"))
	if len(paths) != 0 {
		t.Fatal(paths)
	}
}

func TestGeoUploadCommitsAndOldGenerationCannotResume(t *testing.T) {
	e, plan := startGeo(t)
	target := e.geo.paths["GeoSite.dat"]
	before, _ := os.ReadFile(target)
	t.Cleanup(func() { _ = os.WriteFile(target, before, 0o600) })
	data, err := proto.Marshal(&router.GeoSiteList{Entry: []*router.GeoSite{{CountryCode: "cn", Domain: []*router.Domain{{Type: router.Domain_Domain, Value: "updated.example"}}}}})
	if err != nil {
		t.Fatal(err)
	}
	r := uploadRequest(plan, data)
	for offset := 0; offset < len(data); {
		n := min(7, len(data)-offset)
		r.Offset, r.Data = uint64(offset), data[offset:offset+n]
		if err := e.UploadGeo(context.Background(), r); err != nil {
			t.Fatal(err)
		}
		offset += n
	}
	r.Offset, r.Data, r.Complete = r.Size, nil, true
	if err := e.UploadGeo(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(target)
	if !bytes.Equal(got, data) {
		t.Fatal("not installed")
	}
	updated, _ := e.GetGeoUpdate()
	if updated.Files[0].UpdatedAtUnixMs < time.Now().Add(-time.Minute).UnixMilli() {
		t.Fatal("timestamp not refreshed")
	}
	r.Complete, r.Offset, r.Data = false, 0, data[:1]
	if err := e.UploadGeo(context.Background(), r); err != nil {
		t.Fatal(err)
	}
	partial := e.geo.upload.path
	if err := e.Stop(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(partial); !os.IsNotExist(err) {
		t.Fatal("Stop kept partial upload", err)
	}
	if err := e.UploadGeo(context.Background(), r); err == nil {
		t.Fatal("resumed stopped upload")
	}
}

func TestGeoPlanIgnoresValidationAndUnusedDatabases(t *testing.T) {
	e, plan := startGeo(t)
	if _, err := e.Validate(admission.Profile{Config: []byte("geodata-mode: true\nrules:\n - GEOIP,cn,DIRECT\n")}); err != nil {
		t.Fatal(err)
	}
	after, _ := e.GetGeoUpdate()
	if !proto.Equal(plan, after) {
		t.Fatal("validation replaced active plan")
	}
	if _, err := e.Reload(admission.Profile{Config: []byte("geo-auto-update: true\nrules:\n - MATCH,DIRECT\n")}); err != nil {
		t.Fatal(err)
	}
	after, _ = e.GetGeoUpdate()
	if len(after.Files) != 0 || after.Generation == plan.Generation {
		t.Fatal("unused databases retained", after)
	}
}
