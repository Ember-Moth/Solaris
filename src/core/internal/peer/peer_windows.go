//go:build windows

package peer

import (
	"errors"
	"fmt"
	"net"
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

type handleConn interface{ Fd() uintptr }

// PipeClientVerifier accepts only installedUI, the Solaris.exe beside the service, run in a
// user's session (docs/architecture.md §5.3), and returns that user's SID and session. The
// connection it hands back holds the client's process and executable open until it closes:
// the process ID cannot pass to another process meanwhile, nor can the file be replaced.
// With signer set, as in signed release builds, the client's Authenticode signature must
// be one Windows trusts, by that signer.
func PipeClientVerifier(installedUI, signer string) Verifier {
	return func(conn net.Conn) (net.Conn, Identity, error) {
		pipe, ok := conn.(handleConn)
		if !ok {
			return nil, Identity{}, errors.New("control connection is not a named pipe")
		}
		var processID uint32
		if err := windows.GetNamedPipeClientProcessId(windows.Handle(pipe.Fd()), &processID); err != nil {
			return nil, Identity{}, fmt.Errorf("identify pipe client: %w", err)
		}
		held := &heldHandles{}
		identity, err := verify(processID, installedUI, signer, held)
		if err != nil {
			held.close()
			return nil, Identity{}, err
		}
		return &verifiedConn{Conn: conn, held: held}, identity, nil
	}
}

func verify(processID uint32, installedUI, signer string, held *heldHandles) (Identity, error) {
	process, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, processID)
	if err != nil {
		return Identity{}, fmt.Errorf("open pipe client process: %w", err)
	}
	held.add(process)
	imagePath, err := processImage(process)
	if err != nil {
		return Identity{}, err
	}
	image, err := openLocked(imagePath)
	if err != nil {
		return Identity{}, fmt.Errorf("open pipe client executable: %w", err)
	}
	held.add(image)
	installed, err := openLocked(installedUI)
	if err != nil {
		return Identity{}, fmt.Errorf("open the installed UI: %w", err)
	}
	defer windows.CloseHandle(installed)
	if same, err := sameFile(image, installed); err != nil {
		return Identity{}, err
	} else if !same {
		return Identity{}, fmt.Errorf("pipe client %s is not the installed UI", imagePath)
	}
	if signer != "" {
		name, err := TrustedSigner(imagePath, image)
		if err != nil {
			return Identity{}, fmt.Errorf("pipe client signature: %w", err)
		}
		if name != signer {
			return Identity{}, fmt.Errorf("pipe client is signed by %q, not %q", name, signer)
		}
	}
	var sessionID uint32
	if err := windows.ProcessIdToSessionId(processID, &sessionID); err != nil {
		return Identity{}, fmt.Errorf("read pipe client session: %w", err)
	}
	if sessionID == 0 {
		return Identity{}, errors.New("pipe client is not in a user's session")
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
	// Had the process exited before its handle was open, the ID could be another's by now.
	if event, err := windows.WaitForSingleObject(process, 0); err != nil || event != uint32(windows.WAIT_TIMEOUT) {
		return Identity{}, errors.New("pipe client exited while it was checked")
	}
	return Identity{UserID: user.User.Sid.String(), SessionID: sessionID, ProcessID: processID}, nil
}

func processImage(process windows.Handle) (string, error) {
	buffer := make([]uint16, windows.MAX_LONG_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(process, 0, &buffer[0], &size); err != nil {
		return "", fmt.Errorf("read pipe client executable: %w", err)
	}
	return windows.UTF16ToString(buffer[:size]), nil
}

// openLocked opens path for reading and lets no one write, rename or delete it while open.
func openLocked(path string) (windows.Handle, error) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}
	return windows.CreateFile(name, windows.GENERIC_READ, windows.FILE_SHARE_READ, nil,
		windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
}

// sameFile compares volume and file IDs, which no path trick can fake.
func sameFile(a, b windows.Handle) (bool, error) {
	var first, second windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(a, &first); err != nil {
		return false, err
	}
	if err := windows.GetFileInformationByHandle(b, &second); err != nil {
		return false, err
	}
	return first.VolumeSerialNumber == second.VolumeSerialNumber &&
		first.FileIndexHigh == second.FileIndexHigh && first.FileIndexLow == second.FileIndexLow, nil
}

type heldHandles struct {
	once    sync.Once
	handles []windows.Handle
}

func (h *heldHandles) add(handle windows.Handle) { h.handles = append(h.handles, handle) }

func (h *heldHandles) close() {
	h.once.Do(func() {
		for _, handle := range h.handles {
			windows.CloseHandle(handle)
		}
	})
}

// verifiedConn lets go of the client's process and executable when it closes.
type verifiedConn struct {
	net.Conn
	held *heldHandles
}

func (c *verifiedConn) Close() error {
	err := c.Conn.Close()
	c.held.close()
	return err
}

var (
	wintrust                      = windows.NewLazySystemDLL("wintrust.dll")
	providerDataFromStateData     = wintrust.NewProc("WTHelperProvDataFromStateData")
	providerSignerFromChain       = wintrust.NewProc("WTHelperGetProvSignerFromChain")
	providerCertificateFromSigner = wintrust.NewProc("WTHelperGetProvCertFromChain")
)

// providerCertificate begins CRYPT_PROVIDER_CERT.
type providerCertificate struct {
	size        uint32
	certificate *windows.CertContext
}

// TrustedSigner is the display name of the certificate that signed file at path, when
// Windows trusts the signature; src/solaris/update/signature.py reads installers the same
// way. Revocation is not checked: that may need the network, and runs on every connection.
func TrustedSigner(path string, file windows.Handle) (string, error) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return "", err
	}
	fileInfo := windows.WinTrustFileInfo{
		Size:     uint32(unsafe.Sizeof(windows.WinTrustFileInfo{})),
		FilePath: name,
		File:     file,
	}
	data := windows.WinTrustData{
		Size:                            uint32(unsafe.Sizeof(windows.WinTrustData{})),
		UIChoice:                        windows.WTD_UI_NONE,
		RevocationChecks:                windows.WTD_REVOKE_NONE,
		UnionChoice:                     windows.WTD_CHOICE_FILE,
		StateAction:                     windows.WTD_STATEACTION_VERIFY,
		FileOrCatalogOrBlobOrSgnrOrCert: unsafe.Pointer(&fileInfo),
		ProvFlags:                       windows.WTD_CACHE_ONLY_URL_RETRIEVAL | windows.WTD_REVOCATION_CHECK_NONE,
		UIContext:                       windows.WTD_UICONTEXT_EXECUTE,
	}
	action := windows.WINTRUST_ACTION_GENERIC_VERIFY_V2
	verifyErr := windows.WinVerifyTrustEx(windows.InvalidHWND, &action, &data)
	defer func() {
		data.StateAction = windows.WTD_STATEACTION_CLOSE
		windows.WinVerifyTrustEx(windows.InvalidHWND, &action, &data)
	}()
	if verifyErr != nil {
		return "", verifyErr
	}
	provider, _, _ := providerDataFromStateData.Call(uintptr(data.StateData))
	if provider == 0 {
		return "", errors.New("no signature provider data")
	}
	signer, _, _ := providerSignerFromChain.Call(provider, 0, 0, 0)
	if signer == 0 {
		return "", errors.New("no signer")
	}
	certificate, _, _ := providerCertificateFromSigner.Call(signer, 0)
	if certificate == 0 {
		return "", errors.New("no signer certificate")
	}
	// The address WinTrust returned, read as the structure it points to.
	context := (*(**providerCertificate)(unsafe.Pointer(&certificate))).certificate
	if context == nil {
		return "", errors.New("no signer certificate")
	}
	size := windows.CertGetNameString(context, windows.CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, nil, nil, 0)
	buffer := make([]uint16, max(size, 1))
	windows.CertGetNameString(context, windows.CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, nil, &buffer[0], uint32(len(buffer)))
	return windows.UTF16ToString(buffer), nil
}
