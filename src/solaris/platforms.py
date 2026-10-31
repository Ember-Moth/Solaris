"""Architecture names used for native Windows installation and updates."""

from __future__ import annotations

import ctypes
import platform
import sys


def normalize_machine(machine: str) -> str:
    name = machine.lower()
    if name in ("amd64", "x86_64", "x64"):
        return "x64"
    if name in ("arm64", "aarch64"):
        return "arm64"
    return name


def _windows_native_machine() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        query = kernel.IsWow64Process2
        query.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.POINTER(ctypes.c_ushort),
        ]
        query.restype = ctypes.c_int
        process, native = ctypes.c_ushort(), ctypes.c_ushort()
        if query(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return {0x8664: "x64", 0xAA64: "arm64"}.get(native.value)
    except AttributeError, OSError:
        pass
    return None


def native_machine(system: str = sys.platform) -> str:
    """Prefer the OS architecture, including when x64 Solaris runs under WoA emulation."""
    if system == "win32" and (machine := _windows_native_machine()):
        return machine
    return normalize_machine(platform.machine())
