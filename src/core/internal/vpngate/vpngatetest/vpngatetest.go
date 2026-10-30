// Package vpngatetest writes VPN Gate's server list as its API does, for tests that must
// never reach the real one.
package vpngatetest

import (
	"bytes"
	"encoding/base64"
	"encoding/csv"
	"fmt"
	"strings"
)

// pemBlock reads as PEM, which is all the outbound checks of its files before it dials.
func pemBlock(kind, seed string) string {
	body := base64.StdEncoding.EncodeToString([]byte(strings.Repeat(seed, 48)))
	var lines []string
	for len(body) > 64 {
		lines = append(lines, body[:64])
		body = body[64:]
	}
	lines = append(lines, body)
	return "-----BEGIN " + kind + "-----\n" + strings.Join(lines, "\n") + "\n-----END " + kind + "-----\n"
}

// Config is an OpenVPN configuration as VPN Gate's servers write theirs: TCP, AES-128-CBC
// with SHA1, a CA and a client certificate inline. extra lines go before the files.
func Config(remote string, port int, extra ...string) string {
	lines := []string{
		"###############################################################################",
		"# OpenVPN 2.0 Sample Configuration File",
		"# for PacketiX VPN / SoftEther VPN Server",
		"dev tun",
		"proto tcp",
		fmt.Sprintf("remote %s %d", remote, port),
		";http-proxy-retry",
		";http-proxy [proxy server] [proxy port]",
		"cipher AES-128-CBC",
		"data-ciphers AES-128-CBC",
		"auth SHA1",
		"resolv-retry infinite",
		"nobind",
		"persist-key",
		"persist-tun",
		"client",
		"verb 3",
		"#auth-user-pass",
	}
	lines = append(lines, extra...)
	lines = append(lines,
		"<ca>", strings.TrimSpace(pemBlock("CERTIFICATE", "ca")), "</ca>",
		"",
		"<cert>", strings.TrimSpace(pemBlock("CERTIFICATE", "client")), "</cert>",
		"",
		"<key>", strings.TrimSpace(pemBlock("RSA PRIVATE KEY", "key")), "</key>",
	)
	return strings.Join(lines, "\r\n") + "\r\n"
}

// Server is one row of the list.
type Server struct {
	Host     string
	IP       string
	Score    int64
	Country  string // CountryShort; CountryLong is made up from it
	Operator string
	Message  string
	Config   string // the OpenVPN configuration; Config(IP, 443) when empty
}

// List is the list's CSV, with its first and last lines, as the API writes it; a field with
// a comma or a quote is quoted.
func List(servers ...Server) []byte {
	var out bytes.Buffer
	out.WriteString("*vpn_servers\r\n")
	out.WriteString("#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,Uptime,TotalUsers,TotalTraffic,LogType,Operator,Message,OpenVPN_ConfigData_Base64\r\n")
	rows := csv.NewWriter(&out)
	rows.UseCRLF = true
	for _, s := range servers {
		config := s.Config
		if config == "" {
			config = Config(s.IP, 443)
		}
		_ = rows.Write([]string{
			s.Host, s.IP, fmt.Sprint(s.Score), "12", "84012345", "Country " + s.Country, s.Country,
			"7", "123456789", "4567", "8901234567", "2weeks", s.Operator, s.Message,
			base64.StdEncoding.EncodeToString([]byte(config)),
		})
	}
	rows.Flush()
	out.WriteString("*\r\n")
	return out.Bytes()
}
