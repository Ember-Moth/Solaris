package control

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"

	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/engine"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/frame"
	"github.com/Ember-Moth/mihomo-desktop/src/core/internal/peer"
)

// TestSharedVectors answers the request Python encodes and must produce the exact
// response bytes Python expects (testdata/control-vectors.json).
func TestSharedVectors(t *testing.T) {
	raw, err := os.ReadFile("../../../../testdata/control-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors struct {
		Request  string `json:"get_info_request_frame"`
		Response string `json:"get_info_response_frame"`
	}
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	requestFrame, err := hex.DecodeString(vectors.Request)
	if err != nil {
		t.Fatal(err)
	}
	want, err := hex.DecodeString(vectors.Response)
	if err != nil {
		t.Fatal(err)
	}

	payload, err := frame.Read(bytes.NewReader(requestFrame), frame.KindRequest)
	if err != nil {
		t.Fatal(err)
	}
	dispatcher := New(Options{Engine: engine.Unlinked{}, HostMethods: true})
	var got bytes.Buffer
	if err := frame.Write(&got, frame.KindResponse, dispatcher.Handle(context.Background(), peer.Identity{}, payload)); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got.Bytes(), want) {
		t.Fatalf("response frame = %x, want %x", got.Bytes(), want)
	}
}
