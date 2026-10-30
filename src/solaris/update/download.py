"""Downloading an installer and checking it against the release's ``SHA256SUMS``
(docs/release.md §3). Blocking: run on a worker thread."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import re
import shutil
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from solaris.update.releases import (
    MAX_INSTALLER_BYTES,
    TIMEOUT_S,
    Asset,
    UpdateError,
    open_url,
    read_limited,
)

MAX_CHECKSUMS_BYTES = 64 * 1024
CHUNK_BYTES = 256 * 1024
PARTIAL_SUFFIX = ".part"

# ``sha256sum`` output: the digest, a space, then a space (text) or ``*`` (binary) and the name.
_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")

# Called with the bytes received so far and the size the release declares.
type Progress = Callable[[int, int], None]


def parse_checksums(text: str) -> dict[str, str]:
    """File name to lower-case SHA-256, from ``sha256sum`` output; other lines are skipped."""
    sums = {}
    for line in text.splitlines():
        match = _LINE.fullmatch(line.strip())
        if match is not None:
            sums[match.group(2).strip()] = match.group(1).lower()
    return sums


def fetch_checksums(asset: Asset, *, timeout_s: float = TIMEOUT_S) -> dict[str, str]:
    body = read_limited(open_url(asset.url, timeout_s=timeout_s), MAX_CHECKSUMS_BYTES)
    try:
        return parse_checksums(body.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise UpdateError("checksum_missing", str(error)) from error


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def clear(directory: Path) -> None:
    """Remove earlier downloads; one still in use (a running installer) stays."""
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        with contextlib.suppress(OSError):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()


def download(
    asset: Asset,
    sha256: str,
    directory: Path,
    *,
    progress: Progress | None = None,
    cancelled: threading.Event | None = None,
    timeout_s: float = TIMEOUT_S,
) -> Path:
    """Download ``asset`` into ``directory`` and return its path. It is written beside its
    final name first and renamed only when its size and SHA-256 match; on any failure
    nothing is left behind. ``cancelled`` stops it with ``cancelled``."""
    if asset.size <= 0 or asset.size > MAX_INSTALLER_BYTES:
        raise UpdateError("download_failed", f"the release declares {asset.size} bytes")
    if Path(asset.name).name != asset.name:
        raise UpdateError("bad_release", f"asset name {asset.name!r}")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset.name
    partial = directory / (asset.name + PARTIAL_SUFFIX)
    try:
        digest = _fetch(asset, partial, progress, cancelled, timeout_s)
        if digest != sha256.lower():
            raise UpdateError("checksum_mismatch", f"{asset.name}: {digest}")
        partial.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    return target


def _fetch(
    asset: Asset,
    path: Path,
    progress: Progress | None,
    cancelled: threading.Event | None,
    timeout_s: float,
) -> str:
    digest = hashlib.sha256()
    received = 0
    try:
        response = open_url(asset.url, timeout_s=timeout_s)
    except UpdateError as error:
        raise UpdateError("download_failed", error.message) from error
    try:
        with response, path.open("wb") as file:
            while chunk := response.read(CHUNK_BYTES):
                if cancelled is not None and cancelled.is_set():
                    raise UpdateError("cancelled")
                received += len(chunk)
                if received > asset.size:
                    raise UpdateError("download_failed", f"more than {asset.size} bytes")
                digest.update(chunk)
                file.write(chunk)
                if progress is not None:
                    progress(received, asset.size)
    except (OSError, http.client.HTTPException) as error:
        raise UpdateError("download_failed", str(error) or type(error).__name__) from error
    if received != asset.size:
        raise UpdateError("download_failed", f"{received} of {asset.size} bytes")
    return digest.hexdigest()


def expected_sha256(sums: Mapping[str, str], name: str) -> str:
    sha256 = sums.get(name)
    if sha256 is None:
        raise UpdateError("checksum_missing", name)
    return sha256
