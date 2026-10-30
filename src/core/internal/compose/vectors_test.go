package compose

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"testing"

	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// The core's answers that the UI's tests rely on, shared with tests/test_simulated.py: the
// simulated host stands in for the core there, and checks itself against the same file.

var update = flag.Bool("update", false, "rewrite the answers in testdata/core-vectors.json")

const vectorsPath = "../../../../testdata/core-vectors.json"

type coreVectors struct {
	Comment string          `json:"comment"`
	Compose []composeVector `json:"compose"`
	Inspect []inspectVector `json:"inspect"`
	Preview []previewVector `json:"preview"`
	Convert []convertVector `json:"convert"`
}

// Messages are protojson with the .proto field names; Error is the message the UI gets
// with invalid_config.
type composeVector struct {
	Name        string          `json:"name"`
	Composition json.RawMessage `json:"composition"`
	Report      json.RawMessage `json:"report,omitempty"`
	Error       string          `json:"error,omitempty"`
}

type inspectVector struct {
	Name       string          `json:"name"`
	YAML       string          `json:"yaml"`
	InfoFilter string          `json:"info_filter"`
	Inspection json.RawMessage `json:"inspection,omitempty"`
	Error      string          `json:"error,omitempty"`
}

type previewVector struct {
	Name        string          `json:"name"`
	Composition json.RawMessage `json:"composition"`
	GroupID     string          `json:"group_id"`
	Preview     json.RawMessage `json:"preview,omitempty"`
	Error       string          `json:"error,omitempty"`
}

type convertVector struct {
	Name       string          `json:"name"`
	Request    json.RawMessage `json:"request"`
	Conversion json.RawMessage `json:"conversion,omitempty"`
	Error      string          `json:"error,omitempty"`
}

func TestVectors(t *testing.T) {
	raw, err := os.ReadFile(vectorsPath)
	if err != nil {
		t.Fatal(err)
	}
	var vectors coreVectors
	if err := json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	for i := range vectors.Compose {
		v := &vectors.Compose[i]
		composed, err := Compose(vectorComposition(t, v.Composition))
		var report proto.Message
		if err == nil {
			report = composed.Report
		}
		check(t, v.Name, report, err, &v.Report, &v.Error, &controlpb.CompositionReport{})
	}
	for i := range vectors.Inspect {
		v := &vectors.Inspect[i]
		inspection, err := Inspect([]byte(v.YAML), v.InfoFilter)
		check(t, v.Name, inspection, err, &v.Inspection, &v.Error, &controlpb.InspectSourceResponse{})
	}
	for i := range vectors.Preview {
		v := &vectors.Preview[i]
		preview, err := Preview(vectorComposition(t, v.Composition), v.GroupID)
		check(t, v.Name, preview, err, &v.Preview, &v.Error, &controlpb.PreviewGroupResponse{})
	}
	for i := range vectors.Convert {
		v := &vectors.Convert[i]
		var request controlpb.ConvertToPlanRequest
		if err := protojson.Unmarshal(v.Request, &request); err != nil {
			t.Fatalf("%s: %v", v.Name, err)
		}
		converted, err := Convert([]byte(request.GetYaml()), Conversion{
			SourceID:   request.GetSourceId(),
			Locale:     request.GetLocale(),
			InfoFilter: request.GetInfoFilter(),
		})
		check(t, v.Name, converted, err, &v.Conversion, &v.Error, &controlpb.ConvertToPlanResponse{})
	}
	if *update {
		writeVectors(t, &vectors)
	}
}

// vectorComposition reads a composition as the dispatcher does.
func vectorComposition(t *testing.T, raw json.RawMessage) Composition {
	t.Helper()
	var message controlpb.Composition
	if err := protojson.Unmarshal(raw, &message); err != nil {
		t.Fatal(err)
	}
	composition := Composition{
		InfoFilter: message.GetInfoFilter(),
		Plan:       []byte(message.GetPlanJson()),
		Warp:       message.GetEgress().GetWarp(),
		VpnGate:    message.GetEgress().GetVpnGate(),
		Mesh:       message.GetMesh(),
	}
	for _, source := range message.GetSources() {
		composition.Sources = append(composition.Sources, Source{
			ID:   source.GetId(),
			Name: source.GetName(),
			YAML: []byte(source.GetYaml()),
		})
	}
	return composition
}

// check compares the core's answer with the vector's, or with -update records it.
func check(t *testing.T, name string, got proto.Message, err error, answer *json.RawMessage, failure *string, want proto.Message) {
	t.Helper()
	if *update {
		*answer, *failure = nil, ""
		if err != nil {
			*failure = err.Error()
			return
		}
		data, err := protojson.MarshalOptions{UseProtoNames: true}.Marshal(got)
		if err != nil {
			t.Fatal(err)
		}
		*answer = data
		return
	}
	switch {
	case err != nil && *failure == "":
		t.Errorf("%s: %v", name, err)
	case err != nil:
		if err.Error() != *failure {
			t.Errorf("%s: error %q, want %q", name, err, *failure)
		}
	case *failure != "":
		t.Errorf("%s: no error, want %q", name, *failure)
	default:
		if err := protojson.Unmarshal(*answer, want); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if !proto.Equal(got, want) {
			t.Errorf("%s: the core answers\n%s\nthe vector has\n%s\n(go test ./internal/compose -run TestVectors -update rewrites it)",
				name, protojson.Format(got), protojson.Format(want))
		}
	}
}

func writeVectors(t *testing.T, vectors *coreVectors) {
	t.Helper()
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(vectors); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(vectorsPath, out.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}
