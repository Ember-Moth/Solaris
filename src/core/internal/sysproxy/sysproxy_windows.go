//go:build windows

package sysproxy

import (
	"errors"
	"fmt"
	"runtime"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

// The service writes HKEY_CURRENT_USER as its owner, which needs the predefined handle to
// follow the thread's token rather than stay cached on SYSTEM's hive; as in sing-box's
// boxdd (platform_windows.go).
var (
	wininet                     = windows.NewLazySystemDLL("wininet.dll")
	internetSetOption           = wininet.NewProc("InternetSetOptionW")
	internetQueryOption         = wininet.NewProc("InternetQueryOptionW")
	globalFree                  = windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalFree")
	regDisablePredefinedCacheEx = windows.NewLazySystemDLL("advapi32.dll").NewProc("RegDisablePredefinedCacheEx")
)

const (
	optionRefresh         = 37
	optionSettingsChanged = 39
	optionPerConnection   = 75

	perConnFlags         = 1
	perConnProxyServer   = 2
	perConnProxyBypass   = 3
	perConnAutoConfigURL = 4
)

// perConnOption is INTERNET_PER_CONN_OPTIONW: a DWORD and a union of a DWORD, a string and a
// FILETIME, which uint64 lines up with on 64- and 32-bit Windows alike.
type perConnOption struct {
	option uint32
	value  uint64
}

func (o *perConnOption) text() **uint16 { return (**uint16)(unsafe.Pointer(&o.value)) }

// perConnOptionList is INTERNET_PER_CONN_OPTION_LISTW; a nil connection is the default one.
type perConnOptionList struct {
	size        uint32
	connection  *uint16
	count       uint32
	optionError uint32
	options     *perConnOption
}

// Read returns the proxy settings of the user signed in to session.
func Read(session uint32) (Settings, error) {
	var settings Settings
	err := asUser(session, func() (err error) {
		settings, err = read()
		return err
	})
	return settings, err
}

// Write sets the proxy settings of the user signed in to session and tells WinINet.
func Write(session uint32, settings Settings) error {
	return asUser(session, func() error { return write(settings) })
}

var uncachePredefined = sync.OnceValue(func() error {
	if result, _, _ := regDisablePredefinedCacheEx.Call(); result != 0 {
		return syscall.Errno(result)
	}
	return nil
})

// asUser runs fn on a thread that impersonates the user signed in to session, so that
// HKEY_CURRENT_USER, where WinINet keeps the settings, is theirs and not SYSTEM's.
func asUser(session uint32, fn func() error) error {
	if err := uncachePredefined(); err != nil {
		return fmt.Errorf("disable the predefined registry cache: %w", err)
	}
	var primary windows.Token
	if err := windows.WTSQueryUserToken(session, &primary); err != nil {
		return fmt.Errorf("read the user token of session %d: %w", session, err)
	}
	defer primary.Close()
	var token windows.Token
	if err := windows.DuplicateTokenEx(primary, windows.TOKEN_QUERY|windows.TOKEN_IMPERSONATE, nil,
		windows.SecurityImpersonation, windows.TokenImpersonation, &token); err != nil {
		return fmt.Errorf("duplicate the user token: %w", err)
	}
	defer token.Close()
	done := make(chan error, 1)
	go func() {
		runtime.LockOSThread()
		if err := windows.SetThreadToken(nil, token); err != nil {
			runtime.UnlockOSThread()
			done <- fmt.Errorf("impersonate the user: %w", err)
			return
		}
		err := fn()
		if revert := windows.RevertToSelf(); revert != nil {
			// The thread keeps the user's identity, so it must not run anything else:
			// a goroutine that returns while locked takes its thread with it.
			done <- errors.Join(err, fmt.Errorf("stop impersonating the user: %w", revert))
			return
		}
		runtime.UnlockOSThread()
		done <- err
	}()
	return <-done
}

func read() (Settings, error) {
	options := []perConnOption{
		{option: perConnFlags},
		{option: perConnProxyServer},
		{option: perConnProxyBypass},
		{option: perConnAutoConfigURL},
	}
	list := perConnOptionList{count: uint32(len(options)), options: &options[0]}
	list.size = uint32(unsafe.Sizeof(list))
	size := list.size
	if result, _, err := internetQueryOption.Call(0, optionPerConnection,
		uintptr(unsafe.Pointer(&list)), uintptr(unsafe.Pointer(&size))); result == 0 {
		return Settings{}, fmt.Errorf("read the proxy settings: %w", err)
	}
	return Settings{
		Flags:         uint32(options[0].value),
		Server:        take(options[1].text()),
		Bypass:        take(options[2].text()),
		AutoConfigURL: take(options[3].text()),
	}, nil
}

// take copies a string WinINet allocated and frees it.
func take(text **uint16) string {
	if *text == nil {
		return ""
	}
	defer globalFree.Call(uintptr(unsafe.Pointer(*text)))
	return windows.UTF16PtrToString(*text)
}

func write(settings Settings) error {
	var texts [3]*uint16
	for i, text := range []string{settings.Server, settings.Bypass, settings.AutoConfigURL} {
		var err error
		if texts[i], err = windows.UTF16PtrFromString(text); err != nil {
			return err
		}
	}
	options := []perConnOption{
		{option: perConnFlags, value: uint64(settings.Flags)},
		{option: perConnProxyServer},
		{option: perConnProxyBypass},
		{option: perConnAutoConfigURL},
	}
	for i, text := range texts {
		*options[i+1].text() = text
	}
	list := perConnOptionList{count: uint32(len(options)), options: &options[0]}
	list.size = uint32(unsafe.Sizeof(list))
	result, _, err := internetSetOption.Call(0, optionPerConnection, uintptr(unsafe.Pointer(&list)), uintptr(list.size))
	runtime.KeepAlive(texts)
	if result == 0 {
		return fmt.Errorf("write the proxy settings: %w", err)
	}
	// Running programs pick the change up when told; WinINet re-reads on the next request.
	for _, option := range []uintptr{optionSettingsChanged, optionRefresh} {
		if result, _, err := internetSetOption.Call(0, option, 0, 0); result == 0 {
			return fmt.Errorf("announce the proxy settings: %w", err)
		}
	}
	return nil
}
