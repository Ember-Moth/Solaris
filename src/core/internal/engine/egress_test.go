package engine

import (
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"slices"
	"testing"

	C "github.com/metacubex/mihomo/constant"

	"github.com/Ember-Moth/Solaris/src/core/internal/compose"
	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// warpDevice is a WARP device with keys mihomo reads as Cloudflare's would be; nothing
// ever dials its endpoint.
func warpDevice(t *testing.T, protocol controlpb.WarpProtocol) *controlpb.WarpAccount {
	t.Helper()
	account := &controlpb.WarpAccount{
		DeviceId:  "a1b2c3d4-0000-4000-8000-000000000001",
		Token:     "t0k3n",
		Protocol:  protocol,
		Endpoint:  "162.159.198.1:443",
		AddressV4: "172.16.0.2",
		AddressV6: "2606:4700:110:8a36::2",
		ClientId:  "U4An",
	}
	if protocol == controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD {
		private, _ := ecdh.X25519().GenerateKey(rand.Reader)
		peer, _ := ecdh.X25519().GenerateKey(rand.Reader)
		account.PrivateKey = base64.StdEncoding.EncodeToString(private.Bytes())
		account.PeerPublicKey = base64.StdEncoding.EncodeToString(peer.PublicKey().Bytes())
		account.Endpoint = "162.159.192.7:2408"
		return account
	}
	private, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	peer, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	privateDER, err := x509.MarshalECPrivateKey(private)
	if err != nil {
		t.Fatal(err)
	}
	peerDER, err := x509.MarshalPKIXPublicKey(&peer.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	account.PrivateKey = base64.StdEncoding.EncodeToString(privateDER)
	account.PeerPublicKey = base64.StdEncoding.EncodeToString(peerDER)
	return account
}

func ruleTexts(admitted *admission) []string {
	var rules []string
	for _, rule := range admitted.cfg.Rules {
		rules = append(rules, ruleKeyword(rule.RuleType().String())+","+rule.Payload()+","+rule.Adapter())
	}
	return rules
}

const warpRulePlan = `{"version": 1, "rules": [
	{"id": "ai", "items": [{"type": "DOMAIN-SUFFIX", "value": "openai.com"}], "target": "egress:warp"}],
	"egress": {"warp": {"via": "region:HK"}}}`

// The pinned mihomo builds the exit the composition writes, in each protocol.
func TestWarpExitIsAnOutboundMihomoBuilds(t *testing.T) {
	for protocol, want := range map[controlpb.WarpProtocol]C.AdapterType{
		controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H2: C.Masque,
		controlpb.WarpProtocol_WARP_PROTOCOL_MASQUE_H3: C.Masque,
		controlpb.WarpProtocol_WARP_PROTOCOL_WIREGUARD: C.WireGuard,
	} {
		composed := composition(warpRulePlan)
		composed.Warp = warpDevice(t, protocol)
		admitted, err := admitProfile(Profile{Composition: composed}, TunNone)
		if err != nil {
			t.Fatalf("%v: %v", protocol, err)
		}
		exit, ok := admitted.cfg.Proxies["WARP 出口"]
		if !ok || exit.Type() != want {
			t.Fatalf("%v: the exit is %v", protocol, exit)
		}
		if rules := ruleTexts(admitted); rules[0] != "DOMAIN-SUFFIX,openai.com,WARP 出口" {
			t.Fatalf("%v: rules = %v", protocol, rules)
		}
		if warnings := admitted.report.GetWarnings(); len(warnings) != 0 {
			t.Fatalf("%v: warnings = %v", protocol, warnings)
		}
	}

	// Without a device the rule rejects; the configuration still runs.
	admitted, err := admitProfile(Profile{Composition: composition(warpRulePlan)}, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := admitted.cfg.Proxies["WARP 出口"]; ok {
		t.Fatal("an exit without a device")
	}
	if rules := ruleTexts(admitted); rules[0] != "DOMAIN-SUFFIX,openai.com,REJECT" {
		t.Fatalf("rules = %v", rules)
	}
	if warnings := admitted.report.GetWarnings(); len(warnings) != 1 || warnings[0].GetReason() != compose.ReasonEgressUnavailable {
		t.Fatalf("warnings = %v", warnings)
	}
}

// meshNodes is a subscription with mesh outbounds, one of which serves other devices.
// Tailscale only builds with with_gvisor, so it is left to TestSecurity6.
const meshNodes = `
proxies:
  - {name: 香港 03, type: direct}
  - {name: 公司, type: zerotier, network: 8056c2e21c000001}
  - name: 实验室
    type: easytier
    network-name: lab
    network-secret: s
    peers: ['tcp://198.51.100.1:11010']
    enable-exit-node: true
    proxy-networks: [192.168.1.0/24]
`

func TestMeshOutboundsInAComposition(t *testing.T) {
	composed := composition(`{"version": 1, "rules": [
		{"id": "lab", "items": [{"type": "IP-CIDR", "value": "10.144.0.0/16", "no_resolve": true}], "target": "mesh:m/实验室"},
		{"id": "gone", "items": [{"type": "DOMAIN-SUFFIX", "value": "gone.internal"}], "target": "mesh:m/没了"}]}`)
	composed.Sources = append(composed.Sources, compose.Source{ID: "m", Name: "M", YAML: []byte(meshNodes)})
	admitted, err := admitProfile(Profile{Composition: composed}, TunNone)
	if err != nil {
		t.Fatal(err)
	}
	cfg := admitted.cfg
	for name, want := range map[string]C.AdapterType{"公司": C.ZeroTier, "实验室": C.EasyTier} {
		if proxy, ok := cfg.Proxies[name]; !ok || proxy.Type() != want {
			t.Errorf("%s is %v", name, proxy)
		}
	}
	// The pool's groups never take them.
	for _, group := range []string{"自动选择", "🇭🇰 香港"} {
		for _, member := range members(t, cfg, group) {
			if member == "公司" || member == "实验室" {
				t.Errorf("%s holds %s", group, member)
			}
		}
	}
	rules := ruleTexts(admitted)
	if rules[0] != "IP-CIDR,10.144.0.0/16,实验室" || rules[1] != "DOMAIN-SUFFIX,gone.internal,REJECT" {
		t.Fatalf("rules = %v", rules)
	}
	var got []string
	for _, w := range admitted.report.GetWarnings() {
		got = append(got, w.GetEntryId()+" "+w.GetReason()+" "+w.GetTarget())
	}
	want := []string{
		"gone mesh_missing mesh:m/没了",
		"mesh:m/实验室 mesh_restricted enable-exit-node",
		"mesh:m/实验室 mesh_restricted proxy-networks",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("warnings = %v\nwant %v", got, want)
	}
	if mesh := admitted.report.GetMesh(); len(mesh) != 2 || mesh[1].GetId() != "mesh:m/实验室" || mesh[1].GetType() != "EasyTier" {
		t.Fatalf("mesh = %v", mesh)
	}
}
