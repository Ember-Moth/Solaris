"""Where snapshots go (docs/storage.md §6): the ``BackupProvider`` interface, and the
providers that need no cloud account, one in memory for the tests and one in a local
directory. The iCloud provider (§5, backup_icloud.py) implements the same interface.

A provider holds the snapshots of one account in a space of the app's own (Drive's
appDataFolder, the CloudKit container, a directory), so every snapshot it lists is one of
ours. ``list`` names them newest first; an id is the provider's own name for a snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from solaris.models.backup import BackupError, Snapshot, SnapshotInfo, read_file, write_file

MEMORY = "memory"
DIRECTORY = "directory"
_FILE_NAME = re.compile(r"solaris-backup-\d{8}-\d{6}-[0-9a-f]{12}\.json")


@dataclass(frozen=True)
class BackupEntry:
    id: str
    info: SnapshotInfo
    size: int  # bytes


class BackupProvider(Protocol):
    @property
    def kind(self) -> str:
        """Which provider it is, as backup targets store it: MEMORY, DIRECTORY, …"""
        ...

    async def sign_in(self) -> str:
        """Sign in, asking the user where the provider must; returns the account name."""
        ...

    async def sign_out(self) -> None:
        """Forget the account here; its snapshots stay where they are."""
        ...

    async def account(self) -> str | None:
        """The account signed in, or None."""
        ...

    async def upload(self, snapshot: Snapshot) -> BackupEntry: ...

    async def list(self) -> Sequence[BackupEntry]:
        """The snapshots, newest first."""
        ...

    async def download(self, entry_id: str) -> Snapshot:
        """Raises BackupError (``backup_missing``) for an id it does not hold."""
        ...

    async def delete(self, entry_id: str) -> None:
        """Delete a snapshot; one that is gone already is no error."""
        ...


def _not_signed_in() -> BackupError:
    return BackupError("not_signed_in", "sign in first")


def _missing(entry_id: str) -> BackupError:
    return BackupError("backup_missing", f"no snapshot {entry_id}")


class MemoryProvider:
    """Snapshots in a dict, for the tests; ``fail`` makes the next calls raise it."""

    kind = MEMORY

    def __init__(self, account: str = "user@example.com") -> None:
        self._account = account
        self._signed_in = True
        self._snapshots: dict[str, tuple[bytes, SnapshotInfo]] = {}
        self._uploads = 0
        self.fail: Exception | None = None

    def _ready(self) -> None:
        if self.fail is not None:
            raise self.fail
        if not self._signed_in:
            raise _not_signed_in()

    async def sign_in(self) -> str:
        self._signed_in = True
        return self._account

    async def sign_out(self) -> None:
        self._signed_in = False

    async def account(self) -> str | None:
        return self._account if self._signed_in else None

    async def upload(self, snapshot: Snapshot) -> BackupEntry:
        self._ready()
        self._uploads += 1
        entry_id = f"snapshot-{self._uploads}"
        data = snapshot.encode()
        self._snapshots[entry_id] = (data, snapshot.info)
        return BackupEntry(entry_id, snapshot.info, len(data))

    async def list(self) -> Sequence[BackupEntry]:
        self._ready()
        entries = [
            BackupEntry(entry_id, info, len(data))
            for entry_id, (data, info) in self._snapshots.items()
        ]
        # Uploads that share a second keep their order.
        return sorted(reversed(entries), key=lambda e: e.info.created_at, reverse=True)

    async def download(self, entry_id: str) -> Snapshot:
        self._ready()
        if entry_id not in self._snapshots:
            raise _missing(entry_id)
        return Snapshot.decode(self._snapshots[entry_id][0])

    async def delete(self, entry_id: str) -> None:
        self._ready()
        self._snapshots.pop(entry_id, None)


class DirectoryProvider:
    """Snapshots as files in a directory, such as a folder another program syncs. Files it
    did not write are left alone, and ones that do not read as snapshots are not listed."""

    kind = DIRECTORY

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    async def sign_in(self) -> str:
        await asyncio.to_thread(self.directory.mkdir, parents=True, exist_ok=True)
        return str(self.directory)

    async def sign_out(self) -> None:
        return None

    async def account(self) -> str | None:
        return str(self.directory)

    async def upload(self, snapshot: Snapshot) -> BackupEntry:
        stamp = datetime.fromtimestamp(snapshot.info.created_at, UTC).strftime("%Y%m%d-%H%M%S")
        name = f"solaris-backup-{stamp}-{snapshot.digest[:12]}.json"

        def write() -> int:
            self.directory.mkdir(parents=True, exist_ok=True)
            write_file(self.directory / name, snapshot)
            return (self.directory / name).stat().st_size

        size = await asyncio.to_thread(write)
        return BackupEntry(name, snapshot.info, size)

    async def list(self) -> Sequence[BackupEntry]:
        def read() -> list[BackupEntry]:
            entries = []
            for path in self.directory.glob("solaris-backup-*.json"):
                if not _FILE_NAME.fullmatch(path.name):
                    continue
                with contextlib.suppress(OSError, BackupError):
                    snapshot = read_file(path)
                    entries.append(BackupEntry(path.name, snapshot.info, path.stat().st_size))
            return entries

        entries = await asyncio.to_thread(read)
        return sorted(entries, key=lambda e: (e.info.created_at, e.id), reverse=True)

    def _path(self, entry_id: str) -> Path:
        # Only the names it writes, so an id cannot reach outside the directory.
        if not _FILE_NAME.fullmatch(entry_id):
            raise _missing(entry_id)
        return self.directory / entry_id

    async def download(self, entry_id: str) -> Snapshot:
        path = self._path(entry_id)
        try:
            return await asyncio.to_thread(read_file, path)
        except FileNotFoundError as error:
            raise _missing(entry_id) from error

    async def delete(self, entry_id: str) -> None:
        path = self._path(entry_id)
        await asyncio.to_thread(path.unlink, missing_ok=True)
