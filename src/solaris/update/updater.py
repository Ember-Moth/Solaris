"""The steps of an update, in order (docs/release.md §2, §3): check the latest release, then
download this machine's installer, check it against the release's checksums and its
signature (none on Windows until it is signed, D29), and check it once more right before
the user opens it.

Every method blocks: the ViewModel runs them on a worker thread.
"""

from __future__ import annotations

import platform
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from solaris import __version__
from solaris.update import download
from solaris.update.download import Progress
from solaris.update.releases import (
    CHECKSUMS_NAME,
    Release,
    ReleaseSource,
    UpdateError,
    installer_name,
    is_newer,
)
from solaris.update.signature import Verifier, default_verifier

DIRECTORY_NAME = "updates"  # in the user data directory


@dataclass(frozen=True)
class Check:
    release: Release | None  # None while nothing has been released
    newer: bool  # it is a newer version than this one
    installer: str | None  # the asset this machine would install; None when there is none


@dataclass(frozen=True)
class Downloaded:
    release: Release
    path: Path
    sha256: str
    signed: bool  # False when only the release's checksum vouches for it


class Updater:
    def __init__(
        self,
        data_dir: Path,
        source: ReleaseSource | None = None,
        verifier: Verifier | None = None,
        *,
        current: str = __version__,
        system: str = sys.platform,
        machine: str = platform.machine(),
    ) -> None:
        """``verifier`` defaults to the platform's; without one nothing can be installed."""
        self.directory = data_dir / DIRECTORY_NAME
        self._source = source or ReleaseSource()
        self._verifier = verifier if verifier is not None else default_verifier(system)
        self.current = current
        self._system = system
        self._machine = machine

    def check(self) -> Check:
        release = self._source.latest()
        if release is None:
            return Check(None, False, None)
        installer = installer_name(release.version, self._system, self._machine)
        if self._verifier is None or (installer and release.asset(installer) is None):
            installer = None
        return Check(release, is_newer(release.version, self.current), installer)

    def download(
        self,
        check: Check,
        progress: Progress | None = None,
        cancelled: threading.Event | None = None,
        verifying: Callable[[], None] | None = None,
    ) -> Downloaded:
        """The installer, downloaded and verified; ``verifying`` is called between the
        download and the signature check, which can take a while, when there is one."""
        release = check.release
        asset = release.asset(check.installer) if release and check.installer else None
        if release is None or asset is None or self._verifier is None:
            raise UpdateError("no_installer", release.version if release else "")
        sums_asset = release.asset(CHECKSUMS_NAME)
        if sums_asset is None:
            raise UpdateError("checksum_missing", CHECKSUMS_NAME)
        try:
            sums = download.fetch_checksums(sums_asset)
        except UpdateError as error:
            if error.code == "check_failed":
                raise UpdateError("download_failed", error.message) from error
            raise
        sha256 = download.expected_sha256(sums, asset.name)
        download.clear(self.directory)
        path = download.download(
            asset, sha256, self.directory, progress=progress, cancelled=cancelled
        )
        signed = self._verifier.signed
        if verifying is not None and signed:
            verifying()
        try:
            self._verifier.verify(path)
        except BaseException:
            download.clear(self.directory)
            raise
        return Downloaded(release, path, sha256, signed)

    def ready(self, downloaded: Downloaded) -> Path:
        """The installer to open, after checking that it is still the file verified: it sits
        in a directory the user can write to."""
        try:
            matches = download.sha256_of(downloaded.path) == downloaded.sha256
        except OSError as error:
            raise UpdateError("download_failed", error.strerror or str(error)) from error
        if not matches:
            raise UpdateError("checksum_mismatch", downloaded.path.name)
        return downloaded.path

    def clear(self) -> None:
        download.clear(self.directory)
