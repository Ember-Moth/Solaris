package warp

import (
	"context"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// cloudflare stands in for the registration API on loopback; no test reaches the real one.
type cloudflare struct {
	t *testing.T

	mu       sync.Mutex
	calls    []string
	bodies   []map[string]any
	key      string // the device's public key, as last sent
	enabled  bool
	failWith func(w http.ResponseWriter) bool

	masquePeer string // PEM, as the API sends MASQUE's peer key
}

const (
	deviceID    = "a1b2c3d4-0000-4000-8000-000000000001"
	deviceToken = "t0k3n-0000-4000-8000-000000000001"
	wgPeer      = "bmXOC+F1FxEMF9dyiK2H5/1SUtzH0JuVo51h2wPfgyo="
)

func newCloudflare(t *testing.T) (*cloudflare, *httptest.Server) {
	t.Helper()
	peer, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&peer.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	fake := &cloudflare{t: t, masquePeer: string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))}
	server := httptest.NewServer(fake)
	t.Cleanup(server.Close)
	return fake, server
}

func (c *cloudflare) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls = append(c.calls, r.Method+" "+r.URL.Path)
	if r.Header.Get("User-Agent") != userAgent || r.Header.Get("CF-Client-Version") != clientVersion ||
		r.Header.Get("Content-Type") != contentType {
		c.t.Errorf("%s %s: headers %v", r.Method, r.URL.Path, r.Header)
	}
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		c.t.Errorf("%s %s: body: %v", r.Method, r.URL.Path, err)
	}
	c.bodies = append(c.bodies, body)
	if c.failWith != nil && c.failWith(w) {
		return
	}
	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/"+apiVersion+"/reg":
		if r.Header.Get("Authorization") != "" {
			c.t.Error("registration sent a token")
		}
		c.key = body["key"].(string)
		c.answer(w, body["tunnel_type"].(string), true)
	case r.Method == http.MethodPatch && r.URL.Path == "/"+apiVersion+"/reg/"+deviceID:
		if r.Header.Get("Authorization") != "Bearer "+deviceToken {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"success":false,"errors":[{"code":10000,"message":"Authentication error"}]}`))
			return
		}
		if enabled, ok := body["warp_enabled"]; ok {
			c.enabled = enabled.(bool)
			c.answer(w, "masque", false)
			return
		}
		c.key = body["key"].(string)
		c.answer(w, body["tunnel_type"].(string), false)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

// answer writes the device as the API describes it; only registration carries the token.
func (c *cloudflare) answer(w http.ResponseWriter, tunnel string, withToken bool) {
	peer := map[string]any{
		"public_key": wgPeer,
		"endpoint":   map[string]any{"v4": "162.159.192.7:0", "v6": "[2606:4700:d0::a29f:c007]:0", "host": "engage.cloudflareclient.com:2408"},
	}
	if tunnel == "masque" {
		peer = map[string]any{
			"public_key": c.masquePeer,
			"endpoint":   map[string]any{"v4": "162.159.198.1:0", "v6": "[2606:4700:103::1]:0"},
		}
	}
	device := map[string]any{
		"id":           deviceID,
		"key":          c.key,
		"tunnel_type":  tunnel,
		"warp_enabled": c.enabled,
		"created":      "2026-10-30T08:00:00.123456Z",
		"config": map[string]any{
			"client_id": "U4An",
			"peers":     []any{peer},
			"interface": map[string]any{"addresses": map[string]any{"v4": "172.16.0.2", "v6": "2606:4700:110:8a36::2"}},
		},
	}
	if withToken {
		device["token"] = deviceToken
	}
	_ = json.NewEncoder(w).Encode(device)
}

func register(t *testing.T, server *httptest.Server, request Request) (*controlpb.WarpAccount, error) {
	t.Helper()
	var dialed []string
	client := NewClient(func(ctx context.Context, network, address string) (net.Conn, error) {
		dialed = append(dialed, address)
		return (&net.Dialer{}).DialContext(ctx, network, address)
	})
	account, err := Register(context.Background(), client, server.URL, request)
	if len(dialed) == 0 {
		t.Error("the client never used the dial it was given")
	}
	return account, err
}

func TestRegisterMASQUE(t *testing.T) {
	fake, server := newCloudflare(t)
	account, err := register(t, server, Request{})
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"POST /" + apiVersion + "/reg", "PATCH /" + apiVersion + "/reg/" + deviceID}; strings.Join(fake.calls, ";") != strings.Join(want, ";") {
		t.Fatalf("calls = %v, want %v", fake.calls, want)
	}
	posted := fake.bodies[0]
	if posted["key_type"] != "secp256r1" || posted["tunnel_type"] != "masque" || posted["locale"] != locale ||
		len(posted["install_id"].(string)) != 22 || !strings.HasPrefix(posted["fcm_token"].(string), posted["install_id"].(string)+":APA91b") {
		t.Fatalf("registration = %v", posted)
	}
	if _, err := time.Parse(time.RFC3339, posted["tos"].(string)); err != nil {
		t.Fatalf("tos: %v", err)
	}
	if enable := fake.bodies[1]; len(enable) != 1 || enable["warp_enabled"] != true {
		t.Fatalf("enable = %v", enable)
	}

	// The private key is the one whose public half Cloudflare got, as mihomo reads both.
	privateDER, err := base64.StdEncoding.DecodeString(account.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	private, err := x509.ParseECPrivateKey(privateDER)
	if err != nil {
		t.Fatal(err)
	}
	publicDER, _ := x509.MarshalPKIXPublicKey(&private.PublicKey)
	if base64.StdEncoding.EncodeToString(publicDER) != posted["key"] {
		t.Fatal("the private key does not match the key sent to Cloudflare")
	}
	peerDER, err := base64.StdEncoding.DecodeString(account.PeerPublicKey)
	if err != nil {
		t.Fatal(err)
	}
	if peer, err := x509.ParsePKIXPublicKey(peerDER); err != nil {
		t.Fatalf("peer key: %v", err)
	} else if _, ok := peer.(*ecdsa.PublicKey); !ok {
		t.Fatalf("peer key is a %T", peer)
	}

	want := &controlpb.WarpAccount{
		DeviceId:           deviceID,
		Token:              deviceToken,
		Protocol:           controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2,
		Endpoint:           "162.159.198.1:443",
		AddressV4:          "172.16.0.2",
		AddressV6:          "2606:4700:110:8a36::2",
		ClientId:           "U4An",
		RegisteredAtUnixMs: time.Date(2026, 10, 30, 8, 0, 0, 123456000, time.UTC).UnixMilli(),
	}
	account.PrivateKey, account.PeerPublicKey = "", ""
	if account.String() != want.String() {
		t.Fatalf("account = %v\nwant %v", account, want)
	}
}

func TestRegisterWireGuard(t *testing.T) {
	fake, server := newCloudflare(t)
	account, err := register(t, server, Request{Protocol: controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD})
	if err != nil {
		t.Fatal(err)
	}
	posted := fake.bodies[0]
	if posted["key_type"] != "curve25519" || posted["tunnel_type"] != "wireguard" {
		t.Fatalf("registration = %v", posted)
	}
	privateBytes, err := base64.StdEncoding.DecodeString(account.PrivateKey)
	if err != nil {
		t.Fatal(err)
	}
	private, err := ecdh.X25519().NewPrivateKey(privateBytes)
	if err != nil {
		t.Fatal(err)
	}
	if base64.StdEncoding.EncodeToString(private.PublicKey().Bytes()) != posted["key"] {
		t.Fatal("the private key does not match the key sent to Cloudflare")
	}
	if account.Protocol != controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD || account.PeerPublicKey != wgPeer ||
		account.Endpoint != "162.159.192.7:2408" {
		t.Fatalf("account = %v", account)
	}
	if reserved, err := Reserved(account.ClientId); err != nil || reserved[0] != 83 || reserved[1] != 128 || reserved[2] != 39 {
		t.Fatalf("reserved = %v, %v", reserved, err)
	}
}

// Switching protocols keeps the device: its key changes, and nothing registers anew.
func TestRekeyKeepsTheDevice(t *testing.T) {
	fake, server := newCloudflare(t)
	fake.enabled = true
	account, err := register(t, server, Request{
		Protocol: controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD,
		DeviceID: deviceID,
		Token:    deviceToken,
	})
	if err != nil {
		t.Fatal(err)
	}
	// WARP was already on, so the new key is the only call.
	if len(fake.calls) != 1 || fake.calls[0] != "PATCH /"+apiVersion+"/reg/"+deviceID {
		t.Fatalf("calls = %v", fake.calls)
	}
	if body := fake.bodies[0]; body["key_type"] != "curve25519" || body["tunnel_type"] != "wireguard" || body["key"] == "" {
		t.Fatalf("rekey = %v", body)
	}
	if account.DeviceId != deviceID || account.Token != deviceToken || account.Endpoint != "162.159.192.7:2408" {
		t.Fatalf("account = %v", account)
	}

	if _, err := register(t, server, Request{DeviceID: deviceID, Token: "wrong"}); err == nil ||
		!strings.Contains(err.Error(), "401") || !strings.Contains(err.Error(), "Authentication error") {
		t.Fatalf("a wrong token: %v", err)
	}
	if _, err := Register(context.Background(), http.DefaultClient, server.URL, Request{DeviceID: deviceID}); err == nil {
		t.Fatal("a device without its token was accepted")
	}
}

func TestRegistrationErrorsQuoteCloudflare(t *testing.T) {
	fake, server := newCloudflare(t)
	for _, failure := range []struct {
		name    string
		status  int
		body    string
		limited bool
	}{
		{"429", http.StatusTooManyRequests, "Too Many Requests", true},
		{"1015", http.StatusForbidden, "error code: 1015", true},
		{"invalid", http.StatusBadRequest, `{"success":false,"errors":[{"code":1001,"message":"Invalid public key"}]}`, false},
		{"long", http.StatusInternalServerError, strings.Repeat("é", 2000), false},
	} {
		fake.failWith = func(w http.ResponseWriter) bool {
			w.WriteHeader(failure.status)
			_, _ = w.Write([]byte(failure.body))
			return true
		}
		_, err := register(t, server, Request{})
		if err == nil {
			t.Fatalf("%s: registration succeeded", failure.name)
		}
		if errors.Is(err, ErrRateLimited) != failure.limited {
			t.Errorf("%s: rate limited = %v: %v", failure.name, !failure.limited, err)
		}
		message := err.Error()
		if !failure.limited && failure.name != "long" && !strings.Contains(message, failure.body) {
			t.Errorf("%s: %q does not quote Cloudflare", failure.name, message)
		}
		if len(message) > 2*messageLength+200 {
			t.Errorf("%s: the message runs to %d bytes", failure.name, len(message))
		}
	}

	// WARP must end up on, or the device cannot connect.
	fake.failWith = func(w http.ResponseWriter) bool {
		last := fake.bodies[len(fake.bodies)-1]
		if _, ok := last["warp_enabled"]; ok {
			fake.answer(w, "masque", false)
			return true
		}
		return false
	}
	if _, err := register(t, server, Request{}); err == nil || !strings.Contains(err.Error(), "WARP off") {
		t.Fatalf("WARP left off: %v", err)
	}
}

func TestSplitEndpoint(t *testing.T) {
	for endpoint, want := range map[string]string{
		"162.159.198.1:443":       "162.159.198.1 443",
		"[2606:4700:103::1]:2408": "2606:4700:103::1 2408",
		"162.159.198.1":           "error",
		"162.159.198.1:0":         "error",
	} {
		host, port, err := SplitEndpoint(endpoint)
		got := "error"
		if err == nil {
			got = fmt.Sprintf("%s %d", host, port)
		}
		if got != want {
			t.Errorf("SplitEndpoint(%q) = %q, want %q", endpoint, got, want)
		}
	}
	if _, err := Reserved("AAAAAA=="); err == nil {
		t.Error("a client_id of four bytes gave reserved bytes")
	}
}
