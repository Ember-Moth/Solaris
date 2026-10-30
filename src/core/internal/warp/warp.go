// Package warp registers Cloudflare WARP devices for the WARP exit (docs/exits.md §3.2).
// The API is the one Cloudflare's own clients use, not a public one; the requests follow
// the warp repository's api.sh. The core sends them through one of its groups, so
// Cloudflare sees the node's address and not the user's.
package warp

import (
	"bytes"
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
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// What Cloudflare checks of a client. When it changes them, this block is the one place
// to follow (docs/exits.md §3.2).
const (
	API           = "https://api.cloudflareclient.com"
	apiVersion    = "v0a4471"
	clientVersion = "a-6.35-4471"
	userAgent     = "WARP for Android"
	contentType   = "application/json; charset=UTF-8"
	deviceType    = "Android"
	deviceModel   = "PC"
	locale        = "en-US"

	// The registration answers with port 0; these are the ports the clients dial.
	masquePort    = "443"
	wireguardPort = "2408"
)

// Timeout bounds each call to the API.
const Timeout = 30 * time.Second

// maxAnswer bounds what is read of an answer, and messageLength what an error quotes.
const (
	maxAnswer     = 1 << 20
	messageLength = 1024
)

// ErrRateLimited is Cloudflare refusing registrations for a while, common when many
// people register through the same node.
var ErrRateLimited = errors.New("Cloudflare is limiting how often devices register; try again later, or through another node")

// Request asks for a device with a key for Protocol: a new one, or the existing device
// DeviceID with a new key.
type Request struct {
	Protocol controlpb.WarpProtocol
	DeviceID string
	Token    string
}

// NewClient is an HTTP client whose connections go out through dial, each call bounded
// by Timeout.
func NewClient(dial func(ctx context.Context, network, address string) (net.Conn, error)) *http.Client {
	return &http.Client{
		Timeout: Timeout,
		Transport: &http.Transport{
			DialContext:       dial,
			ForceAttemptHTTP2: true,
		},
	}
}

// key is a key pair as the API and mihomo's outbound take it, both base64.
type key struct {
	private, public string
	kind, tunnel    string // the API's key_type and tunnel_type
}

// newKey makes a key for the protocol: ECDSA P-256 for MASQUE (the private key SEC 1,
// the public key PKIX, both DER), X25519 for WireGuard.
func newKey(protocol controlpb.WarpProtocol) (*key, error) {
	if protocol == controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD {
		private, err := ecdh.X25519().GenerateKey(rand.Reader)
		if err != nil {
			return nil, err
		}
		return &key{
			private: base64.StdEncoding.EncodeToString(private.Bytes()),
			public:  base64.StdEncoding.EncodeToString(private.PublicKey().Bytes()),
			kind:    "curve25519",
			tunnel:  "wireguard",
		}, nil
	}
	private, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	privateDER, err := x509.MarshalECPrivateKey(private)
	if err != nil {
		return nil, err
	}
	publicDER, err := x509.MarshalPKIXPublicKey(&private.PublicKey)
	if err != nil {
		return nil, err
	}
	return &key{
		private: base64.StdEncoding.EncodeToString(privateDER),
		public:  base64.StdEncoding.EncodeToString(publicDER),
		kind:    "secp256r1",
		tunnel:  "masque",
	}, nil
}

// device is what the API answers about a device; the token comes only with registration.
type device struct {
	ID          string `json:"id"`
	Token       string `json:"token"`
	WarpEnabled bool   `json:"warp_enabled"`
	Created     string `json:"created"`
	Config      struct {
		ClientID string `json:"client_id"`
		Peers    []struct {
			PublicKey string `json:"public_key"`
			Endpoint  struct {
				V4   string `json:"v4"`
				V6   string `json:"v6"`
				Host string `json:"host"`
			} `json:"endpoint"`
		} `json:"peers"`
		Interface struct {
			Addresses struct {
				V4 string `json:"v4"`
				V6 string `json:"v6"`
			} `json:"addresses"`
		} `json:"interface"`
	} `json:"config"`
}

// Register makes the device the request asks for and turns WARP on for it: a new device
// is POST /reg, a new key for an existing one PATCH /reg/<id>; either way WARP starts off
// and takes a PATCH of its own. api is the API's base URL.
func Register(ctx context.Context, client *http.Client, api string, request Request) (*controlpb.WarpAccount, error) {
	protocol := request.Protocol
	if protocol == controlpb.WarpProtocol_WARP_PROTOCOL_UNSPECIFIED {
		protocol = controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2
	}
	if (request.DeviceID == "") != (request.Token == "") {
		return nil, errors.New("an existing device needs both its id and its token")
	}
	key, err := newKey(protocol)
	if err != nil {
		return nil, err
	}
	token := request.Token
	var answer *device
	if request.DeviceID == "" {
		installID := randomText(22)
		answer, err = call(ctx, client, http.MethodPost, api+"/"+apiVersion+"/reg", "", map[string]any{
			"key":         key.public,
			"key_type":    key.kind,
			"tunnel_type": key.tunnel,
			"install_id":  installID,
			"fcm_token":   installID + ":APA91b" + randomText(134),
			"tos":         time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
			"type":        deviceType,
			"model":       deviceModel,
			"locale":      locale,
		})
		if err != nil {
			return nil, err
		}
		token = answer.Token
	} else {
		answer, err = call(ctx, client, http.MethodPatch, deviceURL(api, request.DeviceID), token, map[string]any{
			"key":         key.public,
			"key_type":    key.kind,
			"tunnel_type": key.tunnel,
		})
		if err != nil {
			return nil, err
		}
		if answer.ID == "" {
			answer.ID = request.DeviceID
		}
	}
	if answer.ID == "" || token == "" {
		return nil, errors.New("Cloudflare's answer has no device id or token")
	}
	if !answer.WarpEnabled {
		enabled, err := call(ctx, client, http.MethodPatch, deviceURL(api, answer.ID), token, map[string]any{"warp_enabled": true})
		if err != nil {
			return nil, err
		}
		if !enabled.WarpEnabled {
			return nil, errors.New("Cloudflare left WARP off for the device")
		}
	}
	return account(answer, key, token, protocol)
}

func deviceURL(api, id string) string {
	return api + "/" + apiVersion + "/reg/" + id
}

// account takes what the outbound needs from the API's answer.
func account(answer *device, key *key, token string, protocol controlpb.WarpProtocol) (*controlpb.WarpAccount, error) {
	config := answer.Config
	if len(config.Peers) == 0 {
		return nil, errors.New("Cloudflare's answer has no peer")
	}
	peer := config.Peers[0]
	wireguard := protocol == controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD
	peerKey := peer.PublicKey
	if !wireguard {
		// MASQUE's peer key comes as PEM; mihomo takes the DER in base64.
		if block, _ := pem.Decode([]byte(peerKey)); block != nil {
			peerKey = base64.StdEncoding.EncodeToString(block.Bytes)
		}
	}
	host := hostOf(peer.Endpoint.V4)
	if host == "" {
		host = hostOf(peer.Endpoint.V6)
	}
	port := masquePort
	if wireguard {
		port = wireguardPort
		if _, hostPort, err := net.SplitHostPort(peer.Endpoint.Host); err == nil && hostPort != "0" {
			port = hostPort
		}
	}
	addresses := config.Interface.Addresses
	if peerKey == "" || host == "" || addresses.V4 == "" && addresses.V6 == "" {
		return nil, errors.New("Cloudflare's answer lacks the peer's key, its endpoint or the device's addresses")
	}
	registered := time.Now()
	if created, err := time.Parse(time.RFC3339Nano, answer.Created); err == nil {
		registered = created
	}
	return &controlpb.WarpAccount{
		DeviceId:           answer.ID,
		Token:              token,
		Protocol:           protocol,
		PrivateKey:         key.private,
		PeerPublicKey:      peerKey,
		Endpoint:           net.JoinHostPort(host, port),
		AddressV4:          addresses.V4,
		AddressV6:          addresses.V6,
		ClientId:           config.ClientID,
		RegisteredAtUnixMs: registered.UnixMilli(),
	}, nil
}

// hostOf drops the port, and IPv6's brackets, from an endpoint such as
// "162.159.198.1:0" or "[2606:4700:103::1]:0".
func hostOf(endpoint string) string {
	if host, _, err := net.SplitHostPort(endpoint); err == nil {
		return host
	}
	return strings.Trim(endpoint, "[]")
}

// call sends body as JSON and reads the device the API answers with. An answer other
// than 2xx becomes an error quoting Cloudflare.
func call(ctx context.Context, client *http.Client, method, url, token string, body any) (*device, error) {
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("CF-Client-Version", clientVersion)
	request.Header.Set("Content-Type", contentType)
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	text, err := io.ReadAll(io.LimitReader(response.Body, maxAnswer))
	if err != nil {
		return nil, err
	}
	quoted := quote(text)
	if response.StatusCode == http.StatusTooManyRequests || strings.Contains(quoted, "error code: 1015") {
		return nil, fmt.Errorf("%w (Cloudflare: %d %s)", ErrRateLimited, response.StatusCode, quoted)
	}
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, fmt.Errorf("Cloudflare answered %s: %s", response.Status, quoted)
	}
	var answer device
	if err := json.Unmarshal(text, &answer); err != nil {
		return nil, fmt.Errorf("Cloudflare's answer is not a device: %s", quoted)
	}
	return &answer, nil
}

// quote is Cloudflare's text as an error message holds it: trimmed, and cut short.
func quote(text []byte) string {
	quoted := strings.TrimSpace(strings.ToValidUTF8(string(text), "�"))
	if len(quoted) > messageLength {
		cut := messageLength
		for cut > 0 && !utf8.RuneStart(quoted[cut]) {
			cut--
		}
		quoted = quoted[:cut] + "…"
	}
	if quoted == "" {
		return "(no text)"
	}
	return quoted
}

const alphanumeric = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

// randomText is n random letters and digits, as the clients' install ids are.
func randomText(n int) string {
	text := make([]byte, n)
	for i := range text {
		text[i] = alphanumeric[randomIndex(len(alphanumeric))]
	}
	return string(text)
}

func randomIndex(n int) int {
	var b [1]byte
	for {
		if _, err := rand.Read(b[:]); err != nil {
			panic("crypto/rand: " + err.Error())
		}
		// Reject the tail that would favor the first letters.
		if limit := 256 - 256%n; int(b[0]) < limit {
			return int(b[0]) % n
		}
	}
}

// Reserved is WireGuard's three reserved bytes, which Cloudflare reads to route the
// device: client_id, decoded.
func Reserved(clientID string) ([]int, error) {
	decoded, err := base64.StdEncoding.DecodeString(clientID)
	if err != nil {
		return nil, fmt.Errorf("client_id %q is not base64: %w", clientID, err)
	}
	if len(decoded) != 3 {
		return nil, fmt.Errorf("client_id %q holds %d bytes, not 3", clientID, len(decoded))
	}
	return []int{int(decoded[0]), int(decoded[1]), int(decoded[2])}, nil
}

// SplitEndpoint is the endpoint's host and port, as mihomo's outbound takes them.
func SplitEndpoint(endpoint string) (string, int, error) {
	host, port, err := net.SplitHostPort(endpoint)
	if err != nil {
		return "", 0, fmt.Errorf("endpoint %q: %w", endpoint, err)
	}
	number, err := strconv.Atoi(port)
	if err != nil || number <= 0 || number > 0xffff {
		return "", 0, fmt.Errorf("endpoint %q has no usable port", endpoint)
	}
	return host, number, nil
}
