"""An administrator taking over another user's tunnel on Windows (decision D5b,
docs/architecture.md §5.4).

The UI runs with the user's filtered token, which the service does not let take over. Once
the user confirmed it, the UI starts the service's own executable as the take-over helper
through UAC (``ShellExecuteEx`` with the ``runas`` verb); the service accepts that executable
on its pipe only with an administrator's rights. Starting it blocks until the helper is
done: run it on a worker thread.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from solaris.host.base import HostError

SERVICE_EXECUTABLE = "solaris-service.exe"
TAKE_OVER_ARG = "-take-over"

_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION_TYPE = 18  # TOKEN_INFORMATION_CLASS
_ELEVATION_TYPE_FULL, _ELEVATION_TYPE_LIMITED = 2, 3
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC = 0x00000100
_SW_HIDE = 0
_INFINITE = 0xFFFFFFFF
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4
_ERROR_CANCELLED = 1223  # the user declined UAC's prompt


class _ShellExecuteInfo(ctypes.Structure):  # SHELLEXECUTEINFOW
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    )


def service_executable() -> Path:
    """The service's executable beside the installed UI (docs/architecture.md §5.1)."""
    return Path(sys.executable).resolve().parent / SERVICE_EXECUTABLE


def is_administrator() -> bool:
    """Whether the user can confirm an elevation in UAC's consent prompt: an administrator,
    whose UI runs with the filtered token, or with the full one when UAC is off. Others
    would be asked for another account's password, which is not a take-over by them."""
    if sys.platform != "win32":
        return False
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        return False
    try:
        kind = ctypes.c_int()
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_ELEVATION_TYPE,
            ctypes.byref(kind),
            ctypes.sizeof(kind),
            ctypes.byref(size),
        ):
            return False
    finally:
        kernel32.CloseHandle(token)
    if kind.value in (_ELEVATION_TYPE_FULL, _ELEVATION_TYPE_LIMITED):
        return True
    # No split token: UAC is off, or this is a standard user.
    return bool(shell32.IsUserAnAdmin())


def run_elevated(executable: Path, parameters: str) -> int:
    """Run ``executable`` with an administrator's rights once UAC confirmed it, wait for it
    and return its exit status. Raises HostError("cancelled") when the user declines, and
    HostError("take_over_failed") when it does not start."""
    if sys.platform != "win32":
        raise HostError("unsupported_method", "elevation is only on Windows")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole32.CoInitializeEx.restype = ctypes.c_long
    shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(_ShellExecuteInfo),)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = str(executable)
    info.lpParameters = parameters
    info.nShow = _SW_HIDE
    # The shell may hand the verb to COM, which a worker thread has not set up.
    initialized = (
        ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE) >= 0
    )
    try:
        started = shell32.ShellExecuteExW(ctypes.byref(info))
        error = ctypes.get_last_error()
    finally:
        if initialized:
            ole32.CoUninitialize()
    if not started:
        if error == _ERROR_CANCELLED:
            raise HostError("cancelled", "the elevation was declined")
        raise HostError("take_over_failed", ctypes.FormatError(error).strip())
    if not info.hProcess:
        raise HostError("take_over_failed", "the helper did not start")
    try:
        kernel32.WaitForSingleObject(info.hProcess, _INFINITE)
        status = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(status)):
            raise HostError("take_over_failed", ctypes.FormatError(ctypes.get_last_error()))
        return status.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def take_over() -> None:
    """Have the service's take-over helper stop another user's tunnel and make this user the
    owner, after UAC asked. The service restarts for its new owner when it returns."""
    status = run_elevated(service_executable(), TAKE_OVER_ARG)
    if status != 0:
        raise HostError("take_over_failed", f"the take-over helper exited with status {status}")
