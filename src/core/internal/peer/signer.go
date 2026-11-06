package peer

import (
	"crypto/x509"
	"encoding/asn1"
	"errors"
	"fmt"
	"slices"
)

// Signer is who must have signed the UI in a signed release (docs/architecture.md §5.3): the
// display name of the signing certificate's subject, and an extended key usage the certificate
// must carry. Artifact Signing issues a new certificate every day under changing intermediates
// (decision D42), so no certificate or CA is pinned; the usage, which Artifact Signing puts only
// in the certificates of one certificate profile, keeps out another organization of the same
// name. The zero Signer, as in builds without SignedRelease, checks nothing.
type Signer struct {
	Name string
	EKU  string // dotted object identifier
}

// check accepts a signing certificate, its display name and DER encoding, that is s's.
func (s Signer) check(name string, certificate []byte) error {
	if name != s.Name {
		return fmt.Errorf("signed by %q, not %q", name, s.Name)
	}
	usages, err := extendedKeyUsages(certificate)
	if err != nil {
		return err
	}
	if !slices.Contains(usages, s.EKU) {
		return fmt.Errorf("signed by %q without the extended key usage %s", name, s.EKU)
	}
	return nil
}

var extendedKeyUsageExtension = asn1.ObjectIdentifier{2, 5, 29, 37}

// extendedKeyUsages lists the object identifiers in a certificate's extended key usage
// extension, also those crypto/x509 has no name for; none without the extension.
func extendedKeyUsages(certificate []byte) ([]string, error) {
	parsed, err := x509.ParseCertificate(certificate)
	if err != nil {
		return nil, fmt.Errorf("read the signing certificate: %w", err)
	}
	for _, extension := range parsed.Extensions {
		if !extension.Id.Equal(extendedKeyUsageExtension) {
			continue
		}
		var identifiers []asn1.ObjectIdentifier
		if rest, err := asn1.Unmarshal(extension.Value, &identifiers); err != nil || len(rest) != 0 {
			return nil, errors.New("read the signing certificate's extended key usages")
		}
		usages := make([]string, len(identifiers))
		for i, identifier := range identifiers {
			usages[i] = identifier.String()
		}
		return usages, nil
	}
	return nil, nil
}
