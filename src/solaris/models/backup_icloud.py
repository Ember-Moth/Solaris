"""iCloud backup (docs/storage.md §5): snapshots in the CloudKit private database, through the
iCloud calls of the management library (apple/Manager/SolarisManager.h).

The account is the one the system is signed in to. The app can neither sign in nor out of it,
so ``sign_in`` only checks that there is one, and the target shows it as "iCloud": CloudKit
does not tell the app the account's name. Each snapshot is one record, named as the
directory provider names its files (without ``.json``); its SnapshotInfo and size travel as
JSON beside it, so that listing downloads no snapshot.

Only the packaged app carries the library, and only a signed one the entitlements: from
source there is no provider at all (``bundled_provider``), and an unsigned build's library
reports UNSUPPORTED.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from solaris.host.extension import (
    LIBRARY_NAME,
    NOT_FOUND,
    NOT_SIGNED_IN,
    QUOTA_EXCEEDED,
    TIMED_OUT,
    UNREACHABLE,
    ManagerError,
    ManagerLibrary,
    _blocking,  # the same rule: a call in flight must not hold up the app's exit
)
from solaris.models.backup import BackupError, Counts, Snapshot, SnapshotInfo
from solaris.models.backup_providers import BackupEntry

ICLOUD = "icloud"
ACCOUNT = "iCloud"

# SolarisICloudStatus
UNSUPPORTED = 0
AVAILABLE = 1
NO_ACCOUNT = 2
RESTRICTED = 3
TEMPORARILY_UNAVAILABLE = 4
COULD_NOT_DETERMINE = 5

STATUS_TIMEOUT_S = 15.0
# A snapshot is a few KiB, at most 64 MiB (backup.MAX_SNAPSHOT_BYTES).
TRANSFER_TIMEOUT_S = 120.0

_NAME = re.compile(r"solaris-backup-\d{8}-\d{6}-[0-9a-f]{12}")
# The SolarisManagerResult of a failed iCloud call → BackupError code.
_CODES = {
    NOT_SIGNED_IN: "not_signed_in",
    NOT_FOUND: "backup_missing",
    QUOTA_EXCEEDED: "cloud_full",
    UNREACHABLE: "cloud_unreachable",
    TIMED_OUT: "cloud_unreachable",
}


class ICloud(Protocol):
    """The iCloud calls of SolarisManager.h (ManagerLibrary has them). Each blocks until it
    finishes or times out and raises ManagerError unless it returns OK."""

    def icloud_status(self, timeout: float) -> int:
        """A SolarisICloudStatus."""
        ...

    def icloud_save(self, name: str, info: str, snapshot: bytes, timeout: float) -> None: ...

    def icloud_list(self, timeout: float) -> str:
        """A JSON array of {"name", "info"}, one for each snapshot, in no order."""
        ...

    def icloud_download(self, name: str, timeout: float) -> bytes: ...

    def icloud_delete(self, name: str, timeout: float) -> None: ...


def _info(info: SnapshotInfo, size: int) -> str:
    return json.dumps(
        {
            "created_at": info.created_at,
            "device": info.device,
            "app_version": info.app_version,
            "profiles": info.counts.profiles,
            "plans": info.counts.plans,
            "size": size,
        },
        ensure_ascii=False,
    )


def _entry(item: Any) -> BackupEntry | None:
    """A listed record as an entry; None for one that is not a snapshot of ours."""
    try:
        name, info = item["name"], json.loads(item["info"])
        created_at, device, app_version = info["created_at"], info["device"], info["app_version"]
        profiles, plans, size = info["profiles"], info["plans"], info["size"]
    except TypeError, KeyError, ValueError:
        return None
    if not (
        isinstance(name, str)
        and _NAME.fullmatch(name)
        and isinstance(created_at, int | float)
        and isinstance(device, str)
        and isinstance(app_version, str)
        and all(type(n) is int for n in (profiles, plans, size))
    ):
        return None
    return BackupEntry(
        name, SnapshotInfo(created_at, device, app_version, Counts(profiles, plans)), size
    )


class ICloudProvider:
    """BackupProvider over the system's iCloud account."""

    kind = ICLOUD

    def __init__(self, library: ICloud) -> None:
        self._library = library

    async def status(self) -> int:
        """The system's account: a SolarisICloudStatus, COULD_NOT_DETERMINE when asking fails."""
        try:
            return await _blocking(partial(self._library.icloud_status, STATUS_TIMEOUT_S))
        except ManagerError:
            return COULD_NOT_DETERMINE

    async def sign_in(self) -> str:
        if await self.status() != AVAILABLE:
            raise BackupError("not_signed_in", "the system is not signed in to iCloud")
        return ACCOUNT

    async def sign_out(self) -> None:
        return None  # the account is the system's

    async def account(self) -> str | None:
        return ACCOUNT if await self.status() == AVAILABLE else None

    async def upload(self, snapshot: Snapshot) -> BackupEntry:
        stamp = datetime.fromtimestamp(snapshot.info.created_at, UTC).strftime("%Y%m%d-%H%M%S")
        name = f"solaris-backup-{stamp}-{snapshot.digest[:12]}"
        data = snapshot.encode()
        info = _info(snapshot.info, len(data))
        await self._call(self._library.icloud_save, name, info, data, TRANSFER_TIMEOUT_S)
        return BackupEntry(name, snapshot.info, len(data))

    async def list(self) -> Sequence[BackupEntry]:
        listed = await self._call(self._library.icloud_list, TRANSFER_TIMEOUT_S)
        try:
            items = json.loads(listed)
        except ValueError as error:
            raise BackupError("cloud_failed", f"the list is not JSON: {error}") from error
        entries = [e for e in map(_entry, items if isinstance(items, list) else []) if e]
        return sorted(entries, key=lambda e: (e.info.created_at, e.id), reverse=True)

    async def download(self, entry_id: str) -> Snapshot:
        if not _NAME.fullmatch(entry_id):
            raise BackupError("backup_missing", f"no snapshot {entry_id}")
        data = await self._call(self._library.icloud_download, entry_id, TRANSFER_TIMEOUT_S)
        return Snapshot.decode(data)

    async def delete(self, entry_id: str) -> None:
        if _NAME.fullmatch(entry_id):  # no other name is ours
            await self._call(self._library.icloud_delete, entry_id, TRANSFER_TIMEOUT_S)

    async def _call[T](self, call: Callable[..., T], *arguments: Any) -> T:
        try:
            return await _blocking(partial(call, *arguments))
        except ManagerError as error:
            code = _CODES.get(error.result, "cloud_failed")
            raise BackupError(code, error.message) from error


def bundled_provider(executable: Path | None = None) -> ICloudProvider | None:
    """The provider of a packaged Solaris.app, over the manager in Contents/Frameworks beside
    the executable's Contents/MacOS. None on Windows and when the UI runs from source."""
    if sys.platform != "darwin":
        return None
    contents = (executable or Path(sys.executable)).resolve().parent.parent
    library = contents / "Frameworks" / LIBRARY_NAME
    if not library.is_file():
        return None
    return ICloudProvider(ManagerLibrary(library))
