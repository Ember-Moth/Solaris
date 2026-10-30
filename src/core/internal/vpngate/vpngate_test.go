package vpngate

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"testing"

	"github.com/Ember-Moth/Solaris/src/core/internal/vpngate/vpngatetest"
)

// sample is a list with one server of each kind the exit leaves out, and three it keeps.
func sample() []byte {
	config := vpngatetest.Config
	return vpngatetest.List(
		vpngatetest.Server{Host: "vpn100", IP: "203.0.113.10", Score: 100, Country: "JP", Operator: "DESKTOP-1's owner"},
		vpngatetest.Server{Host: "vpn300", IP: "203.0.113.30", Score: 300, Country: "kr", Operator: "PC's owner"},
		vpngatetest.Server{Host: "vpn200", IP: "198.51.100.20", Score: 200, Country: "JP",
			Config: config("198.51.100.20", 995, "comp-lzo")},
		// Left out: UDP, the project's own servers, a private or named remote, a server that
		// wants a password or tls-crypt, TAP, a configuration that is not base64, a second row
		// for the same host, a country that is not a code, a host that cannot be a name.
		vpngatetest.Server{Host: "udp", IP: "203.0.113.40", Score: 900, Country: "JP",
			Config: strings.Replace(config("203.0.113.40", 1195), "proto tcp", "proto udp", 1)},
		vpngatetest.Server{Host: "public-vpn-84", IP: "203.0.113.84", Score: 9000, Country: "JP",
			Operator: "Daiyuu Nobori_ Japan. Academic Use Only."},
		vpngatetest.Server{Host: "private", IP: "192.168.1.2", Score: 900, Country: "JP"},
		vpngatetest.Server{Host: "named", IP: "203.0.113.41", Score: 900, Country: "JP", Config: config("vpn.example", 443)},
		vpngatetest.Server{Host: "password", IP: "203.0.113.42", Score: 900, Country: "JP",
			Config: config("203.0.113.42", 443, "auth-user-pass")},
		vpngatetest.Server{Host: "crypt", IP: "203.0.113.43", Score: 900, Country: "JP",
			Config: config("203.0.113.43", 443, "<tls-crypt>", "x", "</tls-crypt>")},
		vpngatetest.Server{Host: "tap", IP: "203.0.113.44", Score: 900, Country: "JP",
			Config: strings.Replace(config("203.0.113.44", 443), "dev tun", "dev tap", 1)},
		vpngatetest.Server{Host: "vpn100", IP: "203.0.113.11", Score: 50, Country: "JP"},
		vpngatetest.Server{Host: "nowhere", IP: "203.0.113.45", Score: 900, Country: "JPN"},
		vpngatetest.Server{Host: "a,b", IP: "203.0.113.46", Score: 900, Country: "JP"},
	)
}

func TestParseKeepsTheUsableTCPServers(t *testing.T) {
	data := sample()
	// A row whose configuration is not base64.
	data = []byte(strings.Replace(string(data), "*\r\n", "broken,203.0.113.47,900,1,1,Japan,JP,1,1,1,1,2weeks,,,%%%\r\n*\r\n", 1))
	servers, err := Parse(data)
	if err != nil {
		t.Fatal(err)
	}
	var hosts []string
	for _, s := range servers {
		hosts = append(hosts, s.GetHostName())
	}
	if want := []string{"vpn300", "vpn200", "vpn100"}; !slices.Equal(hosts, want) {
		t.Fatalf("servers = %v, want %v", hosts, want)
	}
	first := servers[0]
	if first.GetCountry() != "KR" || first.GetIp() != "203.0.113.30" || first.GetScore() != 300 ||
		first.GetPingMs() != 12 || first.GetSpeedBps() != 84012345 || first.GetSessions() != 7 ||
		first.GetUptimeMs() != 123456789 || first.GetOperator() != "PC's owner" {
		t.Fatalf("first = %v", first)
	}
	options := servers[1].GetOpenvpn()
	if options.GetServer() != "198.51.100.20" || options.GetPort() != 995 || options.GetProto() != "tcp" ||
		options.GetCipher() != "AES-128-CBC" || !slices.Equal(options.GetDataCiphers(), []string{"AES-128-CBC"}) ||
		options.GetAuth() != "SHA1" || options.GetCompLzo() != "yes" {
		t.Fatalf("options = %v", options)
	}
	for name, block := range map[string]string{"ca": options.GetCa(), "cert": options.GetCert(), "key": options.GetKey()} {
		if !strings.HasPrefix(block, "-----BEGIN ") || !strings.HasSuffix(block, "-----\n") {
			t.Errorf("%s = %q", name, block)
		}
	}
	if servers[0].GetOpenvpn().GetCompLzo() != "" {
		t.Fatal("comp-lzo without the option")
	}
}

func TestParseRefusesWhatIsNotAList(t *testing.T) {
	for _, answer := range []string{"", "<html>blocked</html>", "*vpn_servers\r\n*\r\n"} {
		if _, err := Parse([]byte(answer)); err == nil || !strings.Contains(err.Error(), "not a server list") {
			t.Errorf("%q: %v", answer, err)
		}
	}
	// A list whose servers are all left out is no error: the exit has none to use.
	servers, err := Parse(vpngatetest.List(vpngatetest.Server{Host: "p", IP: "10.0.0.1", Country: "JP"}))
	if err != nil || len(servers) != 0 {
		t.Fatalf("servers = %v, %v", servers, err)
	}
}

func TestConvertTakesOnlyKnownOptions(t *testing.T) {
	key := "<tls-auth>\n-----BEGIN OpenVPN Static key V1-----\n" + strings.Repeat("ab", 256) +
		"\n-----END OpenVPN Static key V1-----\n</tls-auth>"
	options, err := Convert(vpngatetest.Config("2a00:1450:4001::5", 443, "remote 203.0.113.9 443", "key-direction 1",
		"script-security 2", "up /bin/sh", key))
	if err != nil {
		t.Fatal(err)
	}
	// The first remote; the scripts are not read, and the outbound has no field for them.
	if options.GetServer() != "2a00:1450:4001::5" || options.GetKeyDirection() != "1" ||
		!strings.Contains(options.GetTlsAuth(), "Static key") {
		t.Fatalf("options = %v", options)
	}
	for _, broken := range []string{
		strings.Replace(vpngatetest.Config("203.0.113.9", 443), "<key>", "", 1),
		strings.Replace(vpngatetest.Config("203.0.113.9", 443), "</key>", "", 1),
		vpngatetest.Config("203.0.113.9", 0),
		vpngatetest.Config("127.0.0.1", 443),
		vpngatetest.Config("fd00::5", 443),
		strings.Replace(vpngatetest.Config("203.0.113.9", 443), "cipher AES-128-CBC", "cipher BF-CBC", 1),
		strings.Replace(vpngatetest.Config("203.0.113.9", 443), "remote 203.0.113.9 443", "", 1),
	} {
		if options, err := Convert(broken); err == nil {
			t.Errorf("converted %v from\n%s", options, broken)
		}
	}
}

// fakeList serves the list on loopback, as long as nothing goes elsewhere.
func fakeList(t *testing.T, status int, body []byte) string {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/iphone/" {
			http.NotFound(w, r)
			return
		}
		w.WriteHeader(status)
		_, _ = w.Write(body)
	}))
	t.Cleanup(server.Close)
	return server.URL + "/api/iphone/"
}

func TestListDownloadsThroughTheDialer(t *testing.T) {
	url := fakeList(t, http.StatusOK, sample())
	dialed := 0
	client := NewClient(func(ctx context.Context, network, address string) (net.Conn, error) {
		dialed++
		return (&net.Dialer{}).DialContext(ctx, network, address)
	})
	servers, err := List(context.Background(), client, url)
	if err != nil {
		t.Fatal(err)
	}
	if len(servers) != 3 || dialed == 0 {
		t.Fatalf("%d servers after %d dials", len(servers), dialed)
	}

	busy := fakeList(t, http.StatusServiceUnavailable, []byte("busy\n"))
	if _, err := List(context.Background(), client, busy); err == nil || err.Error() != "VPN Gate answered 503 Service Unavailable: busy" {
		t.Fatalf("a refusal: %v", err)
	}
}
