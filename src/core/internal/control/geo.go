package control

import (
	"context"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// Kept separate from the base Engine interface for protocol-vector engines which do
// not own a writable data home. Authorization still runs in dispatch before this.
type geoEngine interface {
	GetGeoUpdate() (*controlpb.GetGeoUpdateResponse, error)
	UploadGeo(context.Context, *controlpb.UploadGeoRequest) error
}

func (d *Dispatcher) getGeoUpdate() *controlpb.Response {
	core, ok := d.engine.(geoEngine)
	if !ok {
		return failure(CodeUnsupportedMethod, "GEO updates are unavailable")
	}
	plan, err := core.GetGeoUpdate()
	if err != nil {
		return engineFailure(err)
	}
	return &controlpb.Response{Result: &controlpb.Response_GetGeoUpdate{GetGeoUpdate: plan}}
}

func (d *Dispatcher) uploadGeo(ctx context.Context, request *controlpb.UploadGeoRequest) *controlpb.Response {
	core, ok := d.engine.(geoEngine)
	if !ok {
		return failure(CodeUnsupportedMethod, "GEO updates are unavailable")
	}
	return d.result(core.UploadGeo(ctx, request))
}
