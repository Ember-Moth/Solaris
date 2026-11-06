"""Whether a downloaded installer was signed by Solaris's developer (docs/release.md §3).

macOS asks ``codesign`` and ``spctl`` about the disk image and checks its Team ID; Windows
asks ``WinVerifyTrust`` about the installer and checks the signer's name and the extended key
usage of Solaris's Artifact Signing certificate profile (D42). Both block: run them on a
worker thread. A build without a Windows signer trusts an installer by its checksum alone.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from solaris.update.releases import UpdateError

APPLE_TEAM_ID = "GY9ZKZMAZA"  # apple/README.md
# The Authenticode signer of the Windows installer (D42), as src/core/internal/product's
# WindowsSigner and WindowsSignerEKU name it for the service: the display name of the
# certificate's subject, and an extended key usage that Artifact Signing puts only in the
# certificates of Solaris's certificate profile, which another organization of the same name
# does not get. The certificate itself changes every few days. With no signer, as in 1.0.0,
# the installer is trusted by its SHA-256 in the release's SHA256SUMS alone.
WINDOWS_SIGNER = "Herta Network LLC"
WINDOWS_SIGNER_EKU = "1.3.6.1.4.1.311.97.242074350.326480862.720558710.91084369"

CODESIGN = "/usr/bin/codesign"
SPCTL = "/usr/sbin/spctl"
TOOL_TIMEOUT_S = 120.0

_TEAM = re.compile(r"^TeamIdentifier=(.*)$", re.MULTILINE)


class Verifier(Protocol):
    @property
    def signed(self) -> bool:
        """False when nothing but the release's checksum vouches for the installer."""
        ...

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

    signed = True

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


class _Usage(ctypes.Structure):  # CERT_ENHKEY_USAGE
    _fields_ = (
        ("cUsageIdentifier", ctypes.c_uint32),
        ("rgpszUsageIdentifier", ctypes.POINTER(ctypes.c_char_p)),
    )


# {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
_GENERIC_VERIFY_V2 = (0x00AAC56B, 0xCD44, 0x11D0, (0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
_WTD_UI_NONE = 2
_WTD_REVOKE_WHOLECHAIN = 1
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_VERIFY = 1
_WTD_STATEACTION_CLOSE = 2
_WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x80
_CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
_CERT_FIND_EXT_ONLY_ENHKEY_USAGE_FLAG = 0x2
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1)


@dataclass(frozen=True)
class Signer:
    """The certificate that signed a file, as Windows reports it."""

    name: str  # the subject's display name
    ekus: frozenset[str]  # the extended key usages, dotted


class WindowsVerifier:
    """An Authenticode signature that Windows trusts, checked for revocation along the whole
    chain, by a certificate whose display name is ``signer`` and that has the extended key
    usage ``eku``."""

    signed = True

    def __init__(
        self,
        signer: str,
        eku: str = WINDOWS_SIGNER_EKU,
        read: Callable[[Path], Signer] | None = None,
    ) -> None:
        self._signer = signer
        self._eku = eku
        self._read = read or trusted_signer

    def verify(self, path: Path) -> None:
        found = self._read(path)
        if found.name != self._signer:
            raise UpdateError("signer_mismatch", found.name)
        if self._eku not in found.ekus:
            raise UpdateError("signer_mismatch", f"{found.name} without {self._eku}")


def trusted_signer(path: Path) -> Signer:
    """The certificate that signed ``path``, when Windows trusts the signature; raises
    ``signature_invalid`` with Windows's reason otherwise."""
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
    crypt32.CertGetEnhancedKeyUsage.restype = ctypes.c_int
    crypt32.CertGetEnhancedKeyUsage.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
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
        return Signer(buffer.value, _ekus(crypt32, context))
    finally:
        data.dwStateAction = _WTD_STATEACTION_CLOSE
        verify(_INVALID_HANDLE_VALUE, ctypes.byref(action), ctypes.byref(data))


def _ekus(crypt32: ctypes.CDLL, context: int) -> frozenset[str]:
    """The extended key usages in the certificate's extension; none when it has none."""
    flags = _CERT_FIND_EXT_ONLY_ENHKEY_USAGE_FLAG
    size = ctypes.c_uint32(0)
    if not crypt32.CertGetEnhancedKeyUsage(context, flags, None, ctypes.byref(size)):
        return frozenset()
    buffer = ctypes.create_string_buffer(size.value)
    if not crypt32.CertGetEnhancedKeyUsage(context, flags, buffer, ctypes.byref(size)):
        return frozenset()
    usage = ctypes.cast(buffer, ctypes.POINTER(_Usage)).contents
    identifiers = usage.rgpszUsageIdentifier
    return frozenset(
        (identifiers[index] or b"").decode("ascii") for index in range(usage.cUsageIdentifier)
    )


class ChecksumOnly:
    """An unsigned Windows installer, for builds without a signer, such as 1.0.0. The download has
    already matched its SHA-256 with the release's SHA256SUMS; nothing else vouches for it,
    so the confirmation says it is unsigned."""

    signed = False

    def verify(self, path: Path) -> None:
        pass


def default_verifier(system: str = sys.platform, signer: str = WINDOWS_SIGNER) -> Verifier | None:
    """None where no installer is built (docs/release.md §1)."""
    if system == "darwin":
        return MacVerifier()
    if system == "win32":
        return WindowsVerifier(signer) if signer else ChecksumOnly()
    return None
