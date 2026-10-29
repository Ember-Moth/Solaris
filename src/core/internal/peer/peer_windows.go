//go:build windows

package peer

import (
	"errors"
	"fmt"
	"net"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
)

type handleConn interface{ Fd() uintptr }

// PipeClientVerifier accepts only expectedImage, returning its user SID and session.
// The Authenticode signer and locked file-identity checks from docs/architecture.md
// §5.3 are not implemented yet.
func PipeClientVerifier(expectedImage string) Verifier {
	return func(conn net.Conn) (Identity, error) {
		pipe, ok := conn.(handleConn)
		if !ok {
			return Identity{}, errors.New("control connection is not a named pipe")
		}
		var processID uint32
		if err := windows.GetNamedPipeClientProcessId(windows.Handle(pipe.Fd()), &processID); err != nil {
			return Identity{}, fmt.Errorf("identify pipe client: %w", err)
		}
		process, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, processID)
		if err != nil {
			return Identity{}, fmt.Errorf("open pipe client process: %w", err)
		}
		defer windows.CloseHandle(process)
		image, err := processImage(process)
		if err != nil {
			return Identity{}, err
		}
		if !strings.EqualFold(filepath.Clean(image), filepath.Clean(expectedImage)) {
			return Identity{}, fmt.Errorf("pipe client %s is not the installed UI", image)
		}
		var sessionID uint32
		if err := windows.ProcessIdToSessionId(processID, &sessionID); err != nil {
			return Identity{}, fmt.Errorf("read pipe client session: %w", err)
		}
		var token windows.Token
		if err := windows.OpenProcessToken(process, windows.TOKEN_QUERY, &token); err != nil {
			return Identity{}, fmt.Errorf("open pipe client token: %w", err)
		}
		defer token.Close()
		user, err := token.GetTokenUser()
		if err != nil {
			return Identity{}, fmt.Errorf("read pipe client user: %w", err)
		}
		return Identity{UserID: user.User.Sid.String(), SessionID: sessionID, ProcessID: processID}, nil
	}
}

func processImage(process windows.Handle) (string, error) {
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(process, 0, &buffer[0], &size); err != nil {
		return "", fmt.Errorf("read pipe client executable: %w", err)
	}
	return windows.UTF16ToString(buffer[:size]), nil
}
