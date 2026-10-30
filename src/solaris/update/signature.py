"""Whether a downloaded installer was signed by Solaris's developer (docs/release.md §3).

macOS asks ``codesign`` and ``spctl`` about the disk image and checks its Team ID; Windows
asks ``WinVerifyTrust`` about the installer and checks the signer's name. Both block: run
them on a worker thread.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from solaris.update.releases import UpdateError

APPLE_TEAM_ID = "GY9ZKZMAZA"  # apple/README.md
# A placeholder until the Windows signing plan is decided (roadmap M6): the subject name of
# the Authenticode certificate goes here. Until then every installer fails as signer_mismatch.
WINDOWS_SIGNER = "Ember Moth"

CODESIGN = "/usr/bin/codesign"
SPCTL = "/usr/sbin/spctl"
TOOL_TIMEOUT_S = 120.0

_TEAM = re.compile(r"^TeamIdentifier=(.*)$", re.MULTILINE)


class Verifier(Protocol):
    def verify(self, path: Path) -> None:
        """Return when ``path`` is signed by the expected developer; raise UpdateError
        (``signature_invalid`` or ``signer_mismatch``) otherwise."""
        ...


# Runs a command and returns its exit status and its standard error, where both tools write.
type Runner = Callable[[Sequence[str]], tuple[int, str]]


def run_tool(command: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(command), capture_output=True, text=True, timeout=TOOL_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError("signature_invalid", str(error)) from error
    return done.returncode, done.stderr


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


class MacVerifier:
    """A notarized disk image signed by ``team_id``. The image's signature seals its whole
    contents, so the app inside is not checked separately; Gatekeeper checks it again when
    it is first opened."""

    def __init__(self, team_id: str = APPLE_TEAM_ID, run: Runner = run_tool) -> None:
        self._team_id = team_id
        self._run = run

    def verify(self, path: Path) -> None:
        target = str(path)
        self._require([CODESIGN, "--verify", "--deep", "--strict", "--verbose=2", target])
        status, output = self._run([CODESIGN, "--display", "--verbose=2", target])
        match = _TEAM.search(output) if status == 0 else None
        if match is None:
            raise UpdateError("signature_invalid", _last_line(output))
        team = match.group(1).strip()
        if team != self._team_id:
            raise UpdateError("signer_mismatch", f"TeamIdentifier={team}")
        self._require(
            [
                SPCTL,
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=2",
                target,
            ]
        )

    def _require(self, command: Sequence[str]) -> None:
        status, output = self._run(command)
        if status != 0:
            raise UpdateError("signature_invalid", _last_line(output) or f"exit status {status}")


# -- Windows: WinVerifyTrust ---------------------------------------------------------------


class _Guid(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    )


class _FileInfo(ctypes.Structure):  # WINTRUST_FILE_INFO
    _fields_ = (
        ("cbStruct", ctypes.c_uint32),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.POINTER(_Guid)),
    )


class _TrustData(ctypes.Structure):  # WINTRUST_DATA, with the file member of its union
    _fields_ = (
        ("cbStruct", ctypes.c_uint32),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_uint32),
        ("fdwRevocationChecks", ctypes.c_uint32),
        ("dwUnionChoice", ctypes.c_uint32),
        ("pFile", ctypes.POINTER(_FileInfo)),
        ("dwStateAction", ctypes.c_uint32),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_uint32),
        ("dwUIContext", ctypes.c_uint32),
        ("pSignatureSettings", ctypes.c_void_p),
    )


class _ProviderCert(ctypes.Structure):  # CRYPT_PROVIDER_CERT, the members read here
    _fields_ = (("cbStruct", ctypes.c_uint32), ("pCert", ctypes.c_void_p))


# {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
_GENERIC_VERIFY_V2 = (0x00AAC56B, 0xCD44, 0x11D0, (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
_WTD_UI_NONE = 2
_WTD_REVOKE_WHOLECHAIN = 1
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2
_WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x80
_CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1)


class WindowsVerifier:
    """An Authenticode signature that Windows trusts, checked for revocation along the whole
    chain, whose signer's display name is ``signer``."""

    def __init__(self, signer: str = WINDOWS_SIGNER) -> None:
        self._signer = signer

    def verify(self, path: Path) -> None:
        name = trusted_signer(path)
        if name != self._signer:
            raise UpdateError("signer_mismatch", name)


def trusted_signer(path: Path) -> str:
    """The display name of the certificate that signed ``path``, when Windows trusts the
    signature; raises ``signature_invalid`` with Windows's reason otherwise."""
    if sys.platform != "win32":
        raise UpdateError("signature_invalid", "WinVerifyTrust is only on Windows")
    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    wintrust.WinVerifyTrust.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(_TrustData),
    )
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperProvDataFromStateData.argtypes = (ctypes.c_void_p,)
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    wintrust.WTHelperGetProvCertFromChain.restype = ctypes.POINTER(_ProviderCert)
    wintrust.WTHelperGetProvCertFromChain.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    crypt32.CertGetNameStringW.restype = ctypes.c_uint32
    crypt32.CertGetNameStringW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )

    data1, data2, data3, data4 = _GENERIC_VERIFY_V2
    action = _Guid(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))
    file_info = _FileInfo(ctypes.sizeof(_FileInfo), str(path), None, None)
    data = _TrustData()
    data.cbStruct = ctypes.sizeof(_TrustData)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_WHOLECHAIN
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = _WTD_STATEACTION_VERIFY
    data.dwProvFlags = _WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
    verify = wintrust.WinVerifyTrust
    status = verify(_INVALID_HANDLE_VALUE, ctypes.byref(action), ctypes.byref(data))
    try:
        if status != 0:
            code = status & 0xFFFFFFFF
            reason = ctypes.FormatError(status).strip()
            raise UpdateError("signature_invalid", f"0x{code:08X} {reason}".strip())
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else 0
        cert = wintrust.WTHelperGetProvCertFromChain(signer, 0) if signer else None
        if not cert or not cert.contents.pCert:
            raise UpdateError("signature_invalid", "no signer certificate")
        context = cert.contents.pCert
        size = crypt32.CertGetNameStringW(context, _CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, None, 0)
        buffer = ctypes.create_unicode_buffer(max(size, 1))
        crypt32.CertGetNameStringW(context, _CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, buffer, size)
        return buffer.value
    finally:
        data.dwStateAction = _WTD_STATEACTION_CLOSE
        verify(_INVALID_HANDLE_VALUE, ctypes.byref(action), ctypes.byref(data))


def default_verifier(system: str = sys.platform) -> Verifier | None:
    """None where no installer is built (docs/release.md §1)."""
    if system == "darwin":
        return MacVerifier()
    if system == "win32":
        return WindowsVerifier()
    return None
