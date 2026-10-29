package frame

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

func header(t *testing.T, kind Kind, length uint32) []byte {
	t.Helper()
	var buffer bytes.Buffer
	if err := Write(&buffer, kind, []byte{1}); err != nil {
		t.Fatal(err)
	}
	raw := buffer.Bytes()[:HeaderSize]
	binary.LittleEndian.PutUint32(raw[8:], length)
	return raw
}

func TestRoundTrip(t *testing.T) {
	var buffer bytes.Buffer
	payload := []byte("payload")
	if err := Write(&buffer, KindRequest, payload); err != nil {
		t.Fatal(err)
	}
	got, err := Read(&buffer, KindRequest)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("payload = %q, want %q", got, payload)
	}
}

func TestRejectsBadHeaders(t *testing.T) {
	cases := map[string]func([]byte){
		"magic":   func(raw []byte) { raw[0] = 'X' },
		"version": func(raw []byte) { binary.LittleEndian.PutUint16(raw[4:], 2) },
		"kind":    func(raw []byte) { binary.LittleEndian.PutUint16(raw[6:], uint16(KindResponse)) },
		"empty":   func(raw []byte) { binary.LittleEndian.PutUint32(raw[8:], 0) },
	}
	for name, corrupt := range cases {
		t.Run(name, func(t *testing.T) {
			raw := header(t, KindRequest, 1)
			corrupt(raw)
			_, err := Read(bytes.NewReader(append(raw, 1)), KindRequest)
			if !errors.Is(err, ErrInvalidHeader) {
				t.Fatalf("err = %v, want ErrInvalidHeader", err)
			}
		})
	}
}

func TestOversizedLengthFailsBeforeReadingPayload(t *testing.T) {
	raw := header(t, KindResponse, MaxResponseBytes+1)
	_, err := Read(bytes.NewReader(raw), KindResponse)
	if !errors.Is(err, ErrInvalidHeader) {
		t.Fatalf("err = %v, want ErrInvalidHeader", err)
	}
	if err := Write(io.Discard, KindResponse, make([]byte, MaxResponseBytes+1)); err == nil {
		t.Fatal("Write accepted an oversized response")
	}
}

func TestTruncatedPayload(t *testing.T) {
	raw := header(t, KindRequest, 4)
	_, err := Read(bytes.NewReader(append(raw, 1, 2)), KindRequest)
	if !errors.Is(err, io.ErrUnexpectedEOF) {
		t.Fatalf("err = %v, want io.ErrUnexpectedEOF", err)
	}
}
