"""Automatic backup to the backup targets the user turned on (docs/storage.md §3, §6).

Each target is a row of ``backup_targets``: a provider and its account, with the time of
the last successful upload and the last error. The digest of the snapshot it last took is
this machine's state (``backup_digest:<id>``), so an unchanged database is not uploaded
again. The rules, for every target on its own:

    - after the data changed, wait until it stayed unchanged for DEBOUNCE_S, then upload to
      the targets whose last upload did not fail;
    - at startup and now and then, upload when the last successful upload is older than
      STALE_S, or the last upload failed: a failing target is tried again only then;
    - only upload a snapshot whose digest differs from the last one the target took, and
      never the snapshot of a database with nothing of the user's in it yet;
    - keep the newest KEEP snapshots of the target and delete the older ones.

The timing is the caller's: ``changed`` when the data changed (``Changes`` tells, without the
writes of the backup's own), ``run_due`` on a timer, ``catch_up`` at startup and now and then.
Every method runs on the asyncio thread; the database is read and written on worker threads,
as ProfileLibrary does.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from solaris.models.backup import Backups, Snapshot
from solaris.models.backup_providers import BackupProvider
from solaris.models.database import Database, read_state, write_state

DEBOUNCE_S = 30.0
STALE_S = 24 * 3600.0
KEEP = 10

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    id: str
    provider: str  # BackupProvider.kind
    account: str  # as the settings page shows it
    last_backup_at: float | None = None  # the last successful upload
    last_error: str | None = None  # why the last upload failed; None after a success


@dataclass(frozen=True)
class Outcome:
    target: Target  # as stored after the attempt
    uploaded: bool  # False when the target had this snapshot already, or it failed
    error: Exception | None = None


def _digest_key(target_id: str) -> str:
    return f"backup_digest:{target_id}"


def _call[T](write: Callable[[], T]) -> T:
    return write()


class BackupTargets:
    """The ``backup_targets`` table; a target turned off is marked deleted."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def targets(self) -> tuple[Target, ...]:
        with self._database.reading() as connection:
            rows = connection.execute(
                "SELECT id, provider, account, last_backup_at, last_error FROM backup_targets "
                "WHERE deleted_at IS NULL ORDER BY created_at, id"
            ).fetchall()
        return tuple(Target(*row) for row in rows)

    def target(self, target_id: str) -> Target:
        """Raises KeyError for a target that is not on."""
        found = next((t for t in self.targets() if t.id == target_id), None)
        if found is None:
            raise KeyError(target_id)
        return found

    def add(self, provider: str, account: str) -> Target:
        target = Target(uuid.uuid4().hex, provider, account)
        with self._database.transaction() as connection:
            now = self._database.clock()
            connection.execute(
                "INSERT INTO backup_targets (id, provider, account, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (target.id, provider, account, now, now),
            )
        return target

    def remove(self, target_id: str) -> None:
        with self._database.transaction() as connection:
            now = self._database.clock()
            connection.execute(
                "UPDATE backup_targets SET deleted_at = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, now, target_id),
            )
            connection.execute("DELETE FROM state WHERE key = ?", (_digest_key(target_id),))

    def forget(self, target_id: str) -> Target | None:
        """Forget what the target took, once its snapshots are deleted: the next backup
        uploads, whatever the digest. None for a target turned off meanwhile."""
        with self._database.transaction() as connection:
            now = self._database.clock()
            updated = connection.execute(
                "UPDATE backup_targets SET last_backup_at = NULL, last_error = NULL, "
                "updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now, target_id),
            )
            if updated.rowcount == 0:
                return None
            connection.execute("DELETE FROM state WHERE key = ?", (_digest_key(target_id),))
        return self.target(target_id)

    def digest(self, target_id: str) -> str | None:
        """The digest of the last snapshot the target took."""
        with self._database.reading() as connection:
            return read_state(connection, _digest_key(target_id))

    def record(
        self, target_id: str, *, error: str | None, digest: str | None = None
    ) -> Target | None:
        """Store an attempt: a success (``error`` None) with the digest uploaded, or why it
        failed. A target turned off during the attempt keeps nothing: None."""
        with self._database.transaction() as connection:
            now = self._database.clock()
            if error is None:
                updated = connection.execute(
                    "UPDATE backup_targets SET last_backup_at = ?, last_error = NULL, "
                    "updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (now, now, target_id),
                )
            else:
                updated = connection.execute(
                    "UPDATE backup_targets SET last_error = ?, updated_at = ? "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (error, now, target_id),
                )
            if updated.rowcount == 0:
                return None
            if error is None and digest is not None:
                write_state(connection, _digest_key(target_id), digest, now)
        return self.target(target_id)


class Changes:
    """Whether the database was written since the last look, from SQLite's count of the rows
    its connection inserted, updated or deleted. Every write goes through that one connection
    (database.py), so the count sees them all; the backup's own records, written through
    ``own``, are left out, or every attempt would look like a change and start the next."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._lock = threading.Lock()
        self._seen = self._count()

    def _count(self) -> int:
        with self._database.reading() as connection:
            return connection.total_changes

    def changed(self) -> bool:
        count = self._count()
        with self._lock:
            changed, self._seen = count != self._seen, count
        return changed

    def own[T](self, write: Callable[[], T]) -> T:
        """Run a write of the backup's own; the rows it changes are no change of the data.
        The connection stays held throughout, so no other thread's rows count among them."""
        with self._database.reading() as connection:  # re-entrant: the write takes it again
            before = connection.total_changes
            result = write()
            written = connection.total_changes - before
        with self._lock:
            self._seen += written
        return result


class AutoBackup:
    def __init__(
        self,
        backups: Backups,
        targets: BackupTargets,
        provider: Callable[[Target], BackupProvider | None],
        *,
        changes: Changes | None = None,
        clock: Callable[[], float] = time.time,
        debounce_s: float = DEBOUNCE_S,
        stale_s: float = STALE_S,
        keep: int = KEEP,
    ) -> None:
        """``provider`` gives the provider signed in to a target's account, or None where this
        build has no such provider or the account cannot be used now; those targets wait.
        ``changes`` is told which writes are the backup's own."""
        self._backups = backups
        self._targets = targets
        self._provider = provider
        self._changes = changes
        self._clock = clock
        self._debounce_s = debounce_s
        self._stale_s = stale_s
        self._keep = keep
        self._due_at: float | None = None

    @property
    def due_at(self) -> float | None:
        """When ``run_due`` uploads; None while nothing changed."""
        return self._due_at

    def changed(self) -> None:
        """The data changed: upload once it has stayed unchanged for the debounce time."""
        self._due_at = self._clock() + self._debounce_s

    async def run_due(self) -> list[Outcome]:
        """Upload if the data changed and then stayed unchanged long enough, to the targets
        whose last upload did not fail: those wait for ``catch_up``."""
        if self._due_at is None or self._clock() < self._due_at:
            return []
        self._due_at = None
        targets = await asyncio.to_thread(self._targets.targets)
        healthy = [t for t in targets if t.last_error is None]
        return await self.backup(healthy) if healthy else []

    async def catch_up(self) -> list[Outcome]:
        """Upload to the targets whose last successful upload is too old, or never was, and to
        those whose last upload failed."""
        now = self._clock()
        behind = [
            t
            for t in await asyncio.to_thread(self._targets.targets)
            if t.last_backup_at is None
            or now - t.last_backup_at > self._stale_s
            or t.last_error is not None
        ]
        return await self.backup(behind) if behind else []

    async def backup(
        self, targets: Sequence[Target] | None = None, *, force: bool = False
    ) -> list[Outcome]:
        """Upload a snapshot to ``targets``, every target when None. A target that took a
        snapshot with the same digest is skipped unless ``force``; a failure is stored
        with the target and does not stop the others. An empty database, as on a new
        machine before its restore, is uploaded nowhere: it would only push the newest
        snapshot of the old machine towards deletion."""
        listed = await asyncio.to_thread(self._targets.targets) if targets is None else targets
        chosen = [(t, p) for t in listed if (p := self._provider(t)) is not None]
        if not chosen:
            return []
        snapshot = await asyncio.to_thread(self._backups.snapshot)
        if snapshot.empty:
            return [Outcome(target, uploaded=False) for target, _ in chosen]
        return [await self._upload(t, p, snapshot, force) for t, p in chosen]

    async def _record(
        self, target: Target, *, error: str | None, digest: str | None = None
    ) -> Target | None:
        def write() -> Target | None:
            return self._targets.record(target.id, error=error, digest=digest)

        changes = self._changes
        return await asyncio.to_thread(changes.own if changes else _call, write)

    async def _upload(
        self, target: Target, provider: BackupProvider, snapshot: Snapshot, force: bool
    ) -> Outcome:
        taken = await asyncio.to_thread(self._targets.digest, target.id)
        if not force and taken == snapshot.digest:
            return Outcome(target, uploaded=False)
        try:
            await provider.upload(snapshot)
        except Exception as error:
            stored = await self._record(target, error=str(error))
            return Outcome(stored or target, uploaded=False, error=error)
        stored = await self._record(target, error=None, digest=snapshot.digest)
        if stored is None:  # turned off meanwhile: its snapshots are left alone
            return Outcome(target, uploaded=True)
        await self._prune(target, provider)
        return Outcome(stored, uploaded=True)

    async def _prune(self, target: Target, provider: BackupProvider) -> None:
        """Delete the target's snapshots past the newest ``keep``; the next upload tries
        again where this fails."""
        try:
            entries = await provider.list()
            for entry in entries[self._keep :]:
                await provider.delete(entry.id)
        except Exception as error:
            _log.warning("could not prune the snapshots of %s: %s", target.id, error)
