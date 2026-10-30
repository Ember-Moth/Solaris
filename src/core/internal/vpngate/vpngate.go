// Package vpngate reads VPN Gate's server list for the VPN Gate exit (docs/exits.md §5).
// VPN Gate is an academic experiment of the University of Tsukuba: volunteers run the
// servers, and the project publishes the list as CSV for anyone to build a client with. The
// core fetches it through one of its groups, so VPN Gate sees the node's address and not the
// user's.
//
// The list comes from the network, so its OpenVPN configurations are untrusted: only the
// options the outbound needs are read, and a server whose configuration mihomo would refuse,
// or that needs what is not read, is left out rather than failing the whole configuration.
package vpngate

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	ovpn "github.com/metacubex/mihomo/transport/openvpn"

	"github.com/Ember-Moth/Solaris/src/core/internal/controlpb"
)

// API is the list in the form VPN Gate offers to "make your own VPN Gate client app".
const API = "https://www.vpngate.net/api/iphone/"

// Timeout bounds the download: the list is over a megabyte and VPN Gate's site is slow.
const Timeout = 60 * time.Second

const (
	// maxList bounds what is read of the answer, and messageLength what an error quotes.
	maxList       = 16 << 20
	messageLength = 512
	// MaxServers keeps the answer well inside a response frame.
	MaxServers = 500
	// maxOperator bounds the operator's name, which the UI shows.
	maxOperator = 128
)

// The columns read, by the names of the list's header line.
const (
	columnHost     = "HostName"
	columnIP       = "IP"
	columnScore    = "Score"
	columnPing     = "Ping"
	columnSpeed    = "Speed"
	columnCountry  = "CountryShort"
	columnSessions = "NumVpnSessions"
	columnUptime   = "Uptime"
	columnOperator = "Operator"
	columnMessage  = "Message"
	columnConfig   = "OpenVPN_ConfigData_Base64"
)

var required = []string{columnHost, columnIP, columnScore, columnCountry, columnConfig}

// restricted is how an operator says a server is for academic use only; the project's own
// servers say so (docs/exits.md §5.1), so the exit leaves them to that use.
const restricted = "academic use only"

// NewClient is an HTTP client whose connections go out through dial, bounded by Timeout.
func NewClient(dial func(ctx context.Context, network, address string) (net.Conn, error)) *http.Client {
	return &http.Client{
		Timeout: Timeout,
		Transport: &http.Transport{
			DialContext:       dial,
			ForceAttemptHTTP2: true,
		},
	}
}

// List downloads the list at url and reads it as Parse does.
func List(ctx context.Context, client *http.Client, url string) ([]*controlpb.VpnGateServer, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, maxList+1))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, fmt.Errorf("VPN Gate answered %s: %s", response.Status, quote(data))
	}
	if len(data) > maxList {
		return nil, errors.New("VPN Gate's list is larger than 16 MiB")
	}
	return Parse(data)
}

// Parse reads the list: the TCP OpenVPN servers mihomo can dial, best score first, at most
// MaxServers. A list without its header line is an error; servers it cannot use are left out.
func Parse(data []byte) ([]*controlpb.VpnGateServer, error) {
	data = bytes.TrimPrefix(data, []byte("\xef\xbb\xbf"))
	var columns map[string]int
	var servers []*controlpb.VpnGateServer
	seen := map[string]bool{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimRight(line, "\r")
		switch {
		case line == "" || strings.HasPrefix(line, "*"):
			continue
		case strings.HasPrefix(line, "#"):
			if header, err := fields(line[1:]); err == nil {
				columns = indexOf(header)
			}
			continue
		case columns == nil:
			continue
		}
		row, err := fields(line)
		if err != nil {
			continue
		}
		server, ok := read(row, columns)
		if !ok || seen[server.GetHostName()] {
			continue
		}
		seen[server.GetHostName()] = true
		servers = append(servers, server)
	}
	if columns == nil {
		return nil, fmt.Errorf("VPN Gate's answer is not a server list: %s", quote(data))
	}
	slices.SortStableFunc(servers, func(a, b *controlpb.VpnGateServer) int {
		if a.GetScore() != b.GetScore() {
			if a.GetScore() > b.GetScore() {
				return -1
			}
			return 1
		}
		return strings.Compare(a.GetHostName(), b.GetHostName())
	})
	if len(servers) > MaxServers {
		servers = servers[:MaxServers]
	}
	return servers, nil
}

func fields(line string) ([]string, error) {
	reader := csv.NewReader(strings.NewReader(line))
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true
	return reader.Read()
}

// indexOf maps the header's column names to their positions; nil without the columns read.
func indexOf(header []string) map[string]int {
	columns := map[string]int{}
	for i, name := range header {
		columns[strings.TrimSpace(name)] = i
	}
	for _, name := range required {
		if _, ok := columns[name]; !ok {
			return nil
		}
	}
	return columns
}

// read takes a server from a row of the list; false for one the exit cannot use.
func read(row []string, columns map[string]int) (*controlpb.VpnGateServer, bool) {
	field := func(name string) string {
		if i, ok := columns[name]; ok && i < len(row) {
			return strings.TrimSpace(row[i])
		}
		return ""
	}
	host := field(columnHost)
	country := strings.ToUpper(field(columnCountry))
	if !validHost(host) || !validCountry(country) {
		return nil, false
	}
	operator := clean(field(columnOperator))
	if strings.Contains(strings.ToLower(operator+" "+field(columnMessage)), restricted) {
		return nil, false
	}
	config, err := base64.StdEncoding.DecodeString(strings.Join(strings.Fields(field(columnConfig)), ""))
	if err != nil {
		return nil, false
	}
	options, err := Convert(string(config))
	if err != nil {
		return nil, false
	}
	return &controlpb.VpnGateServer{
		HostName: host,
		Ip:       field(columnIP),
		Country:  country,
		Score:    int64(count(field(columnScore), 63)),
		PingMs:   uint32(count(field(columnPing), 32)),
		SpeedBps: count(field(columnSpeed), 64),
		Sessions: uint32(count(field(columnSessions), 32)),
		UptimeMs: count(field(columnUptime), 64),
		Operator: operator,
		Openvpn:  options,
	}, true
}

// validHost keeps the host name usable in an outbound's name: letters, digits, dots,
// hyphens and underscores.
func validHost(host string) bool {
	if host == "" || len(host) > 64 {
		return false
	}
	for _, r := range host {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '.', r == '-', r == '_':
		default:
			return false
		}
	}
	return true
}

func validCountry(code string) bool {
	return len(code) == 2 && code[0] >= 'A' && code[0] <= 'Z' && code[1] >= 'A' && code[1] <= 'Z'
}

// clean is a name as the UI may show it: valid UTF-8 without control characters, cut short.
func clean(text string) string {
	text = strings.Map(func(r rune) rune {
		if r < ' ' || r == 0x7f {
			return -1
		}
		return r
	}, strings.ToValidUTF8(text, ""))
	if utf8.RuneCountInString(text) > maxOperator {
		text = string([]rune(text)[:maxOperator])
	}
	return strings.TrimSpace(text)
}

// count reads a column the list may leave as "-" or empty; 0 then, and for nonsense.
func count(text string, bits int) uint64 {
	value, err := strconv.ParseUint(text, 10, bits)
	if err != nil {
		return 0
	}
	return value
}

// blocks are the inline files read; a configuration with another one needs what the exit
// does not take, such as tls-crypt.
var blocks = []string{"ca", "cert", "key", "tls-auth"}

// Convert reads an OpenVPN client configuration as mihomo's openvpn outbound takes it. Only
// TCP servers at a public IP address are taken: TCP needs nothing of the via node but TCP,
// and an address keeps DNS out of the way (docs/exits.md §5.2). An error says why not.
func Convert(config string) (*controlpb.OpenVpnOptions, error) {
	options := &controlpb.OpenVpnOptions{}
	inline := map[string]*strings.Builder{}
	block := ""
	remote := ""
	for _, raw := range strings.Split(config, "\n") {
		line := strings.TrimSpace(raw)
		if block != "" {
			if line == "</"+block+">" {
				block = ""
			} else if b, ok := inline[block]; ok {
				b.WriteString(line)
				b.WriteByte('\n')
			}
			continue
		}
		if strings.HasPrefix(line, "<") && strings.HasSuffix(line, ">") && !strings.HasPrefix(line, "</") {
			block = line[1 : len(line)-1]
			if !slices.Contains(blocks, block) {
				return nil, fmt.Errorf("the configuration needs <%s>", block)
			}
			if _, ok := inline[block]; ok {
				return nil, fmt.Errorf("<%s> appears twice", block)
			}
			inline[block] = &strings.Builder{}
			continue
		}
		words := strings.Fields(line)
		if len(words) == 0 || strings.HasPrefix(words[0], "#") || strings.HasPrefix(words[0], ";") {
			continue
		}
		switch name, args := strings.ToLower(words[0]), words[1:]; name {
		case "dev":
			if len(args) == 0 || !strings.HasPrefix(strings.ToLower(args[0]), "tun") {
				return nil, errors.New("only dev tun is supported")
			}
		case "proto":
			if len(args) > 0 {
				options.Proto = args[0]
			}
		case "remote":
			if remote != "" {
				continue // the first one is used
			}
			if len(args) < 2 {
				return nil, errors.New("remote needs a host and a port")
			}
			remote = net.JoinHostPort(args[0], args[1])
			if len(args) > 2 {
				options.Proto = args[2]
			}
		case "cipher":
			if len(args) > 0 {
				options.Cipher = args[0]
			}
		case "data-ciphers":
			if len(args) > 0 {
				options.DataCiphers = strings.Split(args[0], ":")
			}
		case "auth":
			if len(args) > 0 {
				options.Auth = args[0]
			}
		case "comp-lzo":
			options.CompLzo = "yes"
			if len(args) > 0 {
				options.CompLzo = args[0]
			}
		case "key-direction":
			if len(args) > 0 {
				options.KeyDirection = args[0]
			}
		case "auth-user-pass":
			return nil, errors.New("the server wants a user name and a password")
		case "tls-crypt", "tls-crypt-v2":
			return nil, fmt.Errorf("the configuration needs %s", name)
		}
	}
	if block != "" {
		return nil, fmt.Errorf("<%s> is not closed", block)
	}
	if err := setRemote(options, remote); err != nil {
		return nil, err
	}
	for name, field := range map[string]*string{"ca": &options.Ca, "cert": &options.Cert, "key": &options.Key, "tls-auth": &options.TlsAuth} {
		if b, ok := inline[name]; ok {
			*field = b.String()
		}
	}
	if err := check(options); err != nil {
		return nil, err
	}
	return options, nil
}

func setRemote(options *controlpb.OpenVpnOptions, remote string) error {
	if remote == "" {
		return errors.New("the configuration has no remote")
	}
	host, port, err := net.SplitHostPort(remote)
	if err != nil {
		return err
	}
	address, err := netip.ParseAddr(host)
	if err != nil {
		return fmt.Errorf("remote %q is not an IP address", host)
	}
	if !address.IsGlobalUnicast() || address.IsPrivate() {
		return fmt.Errorf("remote %s is not a public address", address)
	}
	number, err := strconv.Atoi(port)
	if err != nil || number <= 0 || number > 0xffff {
		return fmt.Errorf("remote port %q", port)
	}
	options.Server = address.Unmap().String()
	options.Port = uint32(number)
	return nil
}

// check holds the options to what the pinned mihomo accepts, and to TCP.
func check(options *controlpb.OpenVpnOptions) error {
	config := &ovpn.ClientConfig{
		RemoteHost:   options.GetServer(),
		RemotePort:   uint16(options.GetPort()),
		Proto:        options.GetProto(),
		Cipher:       options.GetCipher(),
		DataCiphers:  options.GetDataCiphers(),
		Auth:         options.GetAuth(),
		CompLZO:      options.GetCompLzo(),
		CA:           []byte(options.GetCa()),
		Cert:         []byte(options.GetCert()),
		Key:          []byte(options.GetKey()),
		TLSAuth:      []byte(options.GetTlsAuth()),
		KeyDirection: options.GetKeyDirection(),
	}
	if err := config.Prepare(); err != nil {
		return err
	}
	if config.Proto != ovpn.ProtoTCP {
		return errors.New("only TCP servers are used")
	}
	if len(config.Cert) == 0 {
		return errors.New("the server wants a user name and a password")
	}
	// What the outbound would read, so the composition writes what was checked.
	options.Proto, options.Cipher, options.Auth, options.CompLzo = config.Proto, config.Cipher, config.Auth, config.CompLZO
	return nil
}

// quote is VPN Gate's text as an error message holds it: trimmed, and cut short.
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
