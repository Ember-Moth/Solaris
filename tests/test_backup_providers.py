"""Backup providers, the iCloud one over a stand-in library and the Google Drive one over a
stand-in for Google, and automatic backup on top of them (docs/storage.md §3 to §6)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest

from solaris.models.backup import BackupError, Backups, Snapshot
from solaris.models.backup_auto import AutoBackup, BackupTargets, Changes, Outcome, Target
from solaris.models.backup_icloud import ICloudProvider
from solaris.models.backup_providers import (
    BackupEntry,
    BackupProvider,
    DirectoryProvider,
    MemoryProvider,
)
from solaris.models.profiles import KIND_SUBSCRIPTION, Profile, ProfileList
from solaris.models.store import ProfileStore
from tests.support.credentials import MemoryCredentials
from tests.support.google import FakeGoogle
from tests.support.icloud import FakeICloud


def run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(scenario())


class Clock:
    def __init__(self, now: float = 100_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Data:
    """A database whose content changes on demand."""

    def __init__(self, directory: Path, clock: Clock) -> None:
        self.store = ProfileStore(directory, clock=clock)
        self.backups = Backups(self.store.database, device="laptop", app_version="1.0.0")
        self.targets = BackupTargets(self.store.database)
        self.names = 0

    def change(self) -> None:
        self.names += 1
        profile = Profile(
            id="a", name=f"订阅 {self.names}", kind=KIND_SUBSCRIPTION, url="https://a.example/s"
        )
        self.store.save(ProfileList((profile,), "a"))


def snapshot_at(data: Data, clock: Clock, now: float) -> Snapshot:
    clock.now = now
    data.change()
    return data.backups.snapshot()


# -- providers ---------------------------------------------------------------------------------
@pytest.fixture(params=["memory", "directory", "icloud", "google_drive"])
def provider(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BackupProvider]:
    if request.param == "memory":
        yield MemoryProvider()
    elif request.param == "icloud":
        yield ICloudProvider(FakeICloud())
    elif request.param == "google_drive":
        monkeypatch.setenv("no_proxy", "*")  # the stand-in is on a loopback port
        google = FakeGoogle()
        signed_in = google.provider(MemoryCredentials())
        asyncio.run(signed_in.sign_in())
        yield signed_in
        google.close()
    else:
        yield DirectoryProvider(tmp_path / "backups")


def test_upload_list_download_delete(provider: BackupProvider, tmp_path: Path) -> None:
    clock = Clock()
    data = Data(tmp_path / "data", clock)
    older = snapshot_at(data, clock, 100_000)
    newer = snapshot_at(data, clock, 200_000)

    async def scenario() -> None:
        assert await provider.account() is not None
        first = await provider.upload(older)
        second = await provider.upload(newer)
        assert first.id != second.id
        listed = await provider.list()
        assert [e.id for e in listed] == [second.id, first.id]  # newest first
        assert listed[0].info == newer.info
        assert listed[0].info.device == "laptop"
        assert listed[0].size == len(newer.encode())
        assert await provider.download(first.id) == older
        await provider.delete(first.id)
        await provider.delete(first.id)  # gone already
        assert [e.id for e in await provider.list()] == [second.id]
        with pytest.raises(BackupError) as raised:
            await provider.download(first.id)
        assert raised.value.code == "backup_missing"

    run(scenario)


def test_memory_provider_needs_an_account(tmp_path: Path) -> None:
    provider = MemoryProvider("me@example.com")
    data = Data(tmp_path, Clock())

    async def scenario() -> None:
        await provider.sign_out()
        assert await provider.account() is None
        with pytest.raises(BackupError) as raised:
            await provider.upload(data.backups.snapshot())
        assert raised.value.code == "not_signed_in"
        assert await provider.sign_in() == "me@example.com"
        await provider.upload(data.backups.snapshot())

    run(scenario)


def test_a_directory_lists_only_its_own_snapshots(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    provider = DirectoryProvider(folder)
    data = Data(tmp_path / "data", Clock())

    async def scenario() -> None:
        assert await provider.sign_in() == str(folder)
        entry = await provider.upload(data.backups.snapshot())
        (folder / "notes.json").write_text("{}")
        (folder / "solaris-backup-20260101-000000-000000000000.json").write_text("not json")
        assert [e.id for e in await provider.list()] == [entry.id]
        for name in ("../escape.json", "notes.json", str(folder / entry.id)):
            with pytest.raises(BackupError):
                await provider.download(name)
            with pytest.raises(BackupError):
                await provider.delete(name)
        assert (folder / "notes.json").exists()

    run(scenario)


# -- automatic backup --------------------------------------------------------------------------
class Counting(MemoryProvider):
    """Counts the uploads it was asked for, failed ones included."""

    def __init__(self, account: str) -> None:
        super().__init__(account)
        self.attempts = 0

    async def upload(self, snapshot: Snapshot) -> BackupEntry:
        self.attempts += 1
        return await super().upload(snapshot)


class Scene:
    def __init__(self, directory: Path, *, targets: int = 1) -> None:
        self.clock = Clock()
        self.data = Data(directory, self.clock)
        self.changes = Changes(self.data.store.database)
        self.providers: dict[str, Counting] = {}
        for number in range(targets):
            target = self.data.targets.add("memory", f"user{number}@example.com")
            self.providers[target.id] = Counting(target.account)
        self.auto = AutoBackup(
            self.data.backups,
            self.data.targets,
            self.provider,
            changes=self.changes,
            clock=self.clock,
        )

    def tick(self) -> list[Outcome]:
        """What the settings page's timer does every few seconds."""
        if self.changes.changed():
            self.auto.changed()
        return run(self.auto.run_due)

    def provider(self, target: Target) -> Counting | None:
        return self.providers.get(target.id)

    def at(self, now: float) -> Scene:
        self.clock.now = now
        return self

    def held(self, target: Target | None = None) -> int:
        """How many snapshots the target's provider holds; the only target's by default."""
        provider = self.providers[target.id] if target else next(iter(self.providers.values()))
        return len(run(provider.list))


def test_uploads_once_the_data_stays_unchanged(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.data.change()
    scene.auto.changed()
    assert scene.auto.due_at == 100_030
    assert run(scene.at(100_010).auto.run_due) == []
    scene.data.change()
    scene.auto.changed()  # changed again: wait another 30 s from now
    assert run(scene.at(100_039).auto.run_due) == []
    (outcome,) = run(scene.at(100_040).auto.run_due)
    assert outcome.uploaded
    assert outcome.target.last_backup_at == 100_040
    assert scene.auto.due_at is None
    assert run(scene.at(100_100).auto.run_due) == []  # nothing changed since
    assert scene.held() == 1


def test_an_unchanged_snapshot_is_not_uploaded_again(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.data.change()
    (first,) = run(scene.auto.backup)
    (again,) = run(scene.at(200_000).auto.backup)
    assert (first.uploaded, again.uploaded) == (True, False)
    assert again.target.last_backup_at == 100_000
    (forced,) = run(lambda: scene.auto.backup(force=True))
    assert forced.uploaded
    scene.data.change()
    (changed,) = run(scene.auto.backup)
    assert changed.uploaded
    assert scene.held() == 3


def test_catching_up_after_a_day_or_a_failure(tmp_path: Path) -> None:
    scene = Scene(tmp_path, targets=2)
    first, second = scene.data.targets.targets()
    scene.data.change()
    run(lambda: scene.auto.backup([first]))  # the second never backed up

    (outcome,) = run(scene.at(100_000 + 3600).auto.catch_up)
    assert (outcome.target.id, outcome.uploaded) == (second.id, True)

    scene.data.change()
    assert run(scene.at(100_000 + 23 * 3600).auto.catch_up) == []
    outcomes = run(scene.at(100_000 + 25 * 3600).auto.catch_up)
    assert [(o.target.id, o.uploaded) for o in outcomes] == [(first.id, True)]
    assert (scene.held(first), scene.held(second)) == (2, 1)

    # A failed upload is tried again at the next catch-up, not a day later.
    scene.providers[second.id].fail = BackupError("cloud_unreachable", "offline")
    scene.data.change()
    run(scene.at(100_000 + 25 * 3600 + 60).auto.backup)
    scene.providers[second.id].fail = None
    (retried,) = run(scene.at(100_000 + 26 * 3600).auto.catch_up)
    assert (retried.target.id, retried.uploaded, retried.target.last_error) == (
        second.id,
        True,
        None,
    )


def test_an_empty_database_is_uploaded_nowhere(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    assert scene.data.backups.snapshot().empty
    (outcome,) = run(lambda: scene.auto.backup(force=True))
    assert (outcome.uploaded, outcome.error) == (False, None)
    assert outcome.target.last_backup_at is None
    assert scene.held() == 0
    scene.data.change()
    assert not scene.data.backups.snapshot().empty
    (outcome,) = run(scene.auto.backup)
    assert outcome.uploaded


def test_targets_without_a_provider_wait(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    elsewhere = scene.data.targets.add("drive", "user@example.com")
    scene.data.change()
    outcomes = run(scene.auto.catch_up)
    assert [o.target.provider for o in outcomes] == ["memory"]
    assert scene.data.targets.target(elsewhere.id).last_backup_at is None


def test_forgetting_what_a_target_took(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    (target,) = scene.data.targets.targets()
    scene.data.change()
    run(scene.auto.backup)
    forgotten = scene.data.targets.forget(target.id)
    assert forgotten is not None
    assert (forgotten.last_backup_at, forgotten.last_error) == (None, None)
    assert scene.data.targets.digest(target.id) is None
    (outcome,) = run(scene.auto.backup)  # the same data goes up again
    assert outcome.uploaded


def test_changes_see_every_write_but_the_backups_own(tmp_path: Path) -> None:
    data = Data(tmp_path, Clock())
    changes = Changes(data.store.database)
    assert not changes.changed()
    data.change()
    assert changes.changed()
    assert not changes.changed()
    target = data.targets.add("memory", "user@example.com")
    assert changes.changed()

    assert changes.own(lambda: data.targets.record(target.id, error=None, digest="a")) is not None
    assert not changes.changed()
    data.change()  # the user's change before the backup's own write still counts
    changes.own(lambda: data.targets.record(target.id, error="cloud_full: full"))
    assert changes.changed()


def test_a_success_starts_no_further_upload(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    (provider,) = scene.providers.values()
    scene.data.change()
    assert scene.tick() == []
    (outcome,) = scene.at(100_031).tick()
    assert outcome.uploaded
    for step in range(1, 20):
        assert scene.at(100_031 + step * 31).tick() == []
        assert scene.auto.due_at is None  # the upload's own record is no change
    assert provider.attempts == 1


def test_a_failing_target_waits_for_the_catch_up(tmp_path: Path) -> None:
    scene = Scene(tmp_path, targets=2)
    failing, healthy = scene.data.targets.targets()
    scene.providers[failing.id].fail = BackupError("cloud_unreachable", "offline")
    scene.data.change()
    scene.tick()
    outcomes = scene.at(100_031).tick()
    assert [(o.target.id, o.uploaded) for o in outcomes] == [
        (failing.id, False),
        (healthy.id, True),
    ]

    # Neither its own record of the failure nor the user's next change tries it again.
    for step in range(1, 30):
        if step == 10:
            scene.data.change()
        scene.at(100_031 + step * 31).tick()
    assert scene.providers[failing.id].attempts == 1
    assert scene.providers[healthy.id].attempts == 2

    scene.providers[failing.id].fail = None
    (outcome,) = run(scene.at(100_031 + 3600).auto.catch_up)
    assert (outcome.target.id, outcome.uploaded, outcome.target.last_error) == (
        failing.id,
        True,
        None,
    )


def test_a_target_turned_off_during_an_upload_keeps_nothing(tmp_path: Path) -> None:
    data = Data(tmp_path, Clock())
    target = data.targets.add("memory", "user@example.com")
    data.targets.remove(target.id)
    assert data.targets.record(target.id, error=None, digest="a") is None
    assert data.targets.record(target.id, error="cloud_full: full") is None
    assert data.targets.digest(target.id) is None
    assert data.targets.forget(target.id) is None
    assert data.targets.targets() == ()

    class Removing(MemoryProvider):
        async def upload(self, snapshot: Snapshot) -> BackupEntry:
            data.targets.remove(on.id)  # the user turned it off meanwhile
            return await super().upload(snapshot)

    on = data.targets.add("memory", "user@example.com")
    provider = Removing()
    auto = AutoBackup(data.backups, data.targets, lambda _: provider)
    data.change()
    (outcome,) = run(auto.backup)
    assert (outcome.target.id, outcome.uploaded) == (on.id, True)
    assert data.targets.digest(on.id) is None


def test_keeps_the_newest_ten(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    for number in range(12):
        scene.at(100_000 + number * 60).data.change()
        run(scene.auto.backup)
    (provider,) = scene.providers.values()
    entries = run(provider.list)
    assert len(entries) == 10
    assert entries[-1].info.created_at == 100_000 + 2 * 60


def test_a_failing_target_does_not_stop_the_others(tmp_path: Path) -> None:
    scene = Scene(tmp_path, targets=2)
    first, second = scene.data.targets.targets()
    scene.providers[first.id].fail = BackupError("download_failed", "network timeout")
    scene.data.change()

    failed, uploaded = run(scene.auto.backup)

    assert (failed.uploaded, uploaded.uploaded) == (False, True)
    assert isinstance(failed.error, BackupError)
    assert failed.target.last_error == "download_failed: network timeout"
    assert failed.target.last_backup_at is None
    assert scene.held(second) == 1

    scene.providers[first.id].fail = None
    retried, skipped = run(scene.at(100_300).auto.backup)
    assert (retried.uploaded, skipped.uploaded) == (True, False)
    assert retried.target.last_error is None
    assert retried.target.last_backup_at == 100_300


def test_pruning_failures_keep_the_upload(tmp_path: Path) -> None:
    class Unlistable(Counting):
        async def list(self) -> Any:
            raise BackupError("download_failed", "list failed")

    scene = Scene(tmp_path)
    (target,) = scene.data.targets.targets()
    scene.providers[target.id] = Unlistable(target.account)
    scene.data.change()
    (outcome,) = run(scene.auto.backup)
    assert outcome.uploaded
    assert outcome.target.last_error is None


def test_targets_turned_off(tmp_path: Path) -> None:
    scene = Scene(tmp_path, targets=2)
    first, second = scene.data.targets.targets()
    scene.data.change()
    run(scene.auto.backup)
    assert scene.data.targets.digest(first.id) is not None
    scene.data.targets.remove(first.id)
    assert scene.data.targets.targets() == (scene.data.targets.target(second.id),)
    assert scene.data.targets.digest(first.id) is None
    with pytest.raises(KeyError):
        scene.data.targets.target(first.id)
    assert run(Scene(tmp_path / "none", targets=0).auto.backup) == []
