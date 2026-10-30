"""The iCloud backup provider over a stand-in for the library's iCloud calls (docs/storage.md
§5). The provider contract itself is in test_backup_providers.py; the ctypes binding and the
packaged app's provider are in test_mac_extension_host.py."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from solaris.host.extension import (
    FAILED,
    NOT_SIGNED_IN,
    QUOTA_EXCEEDED,
    TIMED_OUT,
    UNREACHABLE,
    ManagerError,
)
from solaris.models.backup import BackupError, Backups
from solaris.models.backup_icloud import (
    ACCOUNT,
    AVAILABLE,
    COULD_NOT_DETERMINE,
    NO_ACCOUNT,
    ICloudProvider,
)
from solaris.models.profiles import KIND_SUBSCRIPTION, Profile, ProfileList
from solaris.models.store import ProfileStore
from tests.support.icloud import FakeICloud


@pytest.fixture
def backups(tmp_path: Path) -> Backups:
    store = ProfileStore(tmp_path, clock=lambda: 1_800_000_000.0)
    profile = Profile(id="a", name="订阅", kind=KIND_SUBSCRIPTION, url="https://a.example/s")
    store.save(ProfileList((profile,), "a"))
    return Backups(store.database, device="laptop", app_version="0.1.0")


def test_the_account_is_the_systems() -> None:
    icloud = FakeICloud()
    provider = ICloudProvider(icloud)

    async def scenario() -> None:
        assert (await provider.status(), await provider.account()) == (AVAILABLE, ACCOUNT)
        assert await provider.sign_in() == ACCOUNT
        await provider.sign_out()  # nothing to forget: the account stays the system's
        assert await provider.account() == ACCOUNT
        icloud.status = NO_ACCOUNT
        assert await provider.account() is None
        with pytest.raises(BackupError) as raised:
            await provider.sign_in()
        assert raised.value.code == "not_signed_in"
        icloud.failures["status"] = ManagerError(TIMED_OUT, "Timed out")
        assert await provider.status() == COULD_NOT_DETERMINE

    asyncio.run(scenario())


def test_records_carry_their_info(backups: Backups) -> None:
    icloud = FakeICloud()
    provider = ICloudProvider(icloud)
    snapshot = backups.snapshot()

    entry = asyncio.run(provider.upload(snapshot))

    assert entry.id == f"solaris-backup-20270115-080000-{snapshot.digest[:12]}"
    info, data = icloud.records[entry.id]
    assert data == snapshot.encode()
    assert json.loads(info) == {
        "created_at": 1_800_000_000.0,
        "device": "laptop",
        "app_version": "0.1.0",
        "profiles": 1,
        "plans": 0,
        "size": len(data),
    }
    # Listing reads the info alone; the snapshot stays in iCloud.
    calls = len(icloud.calls)
    assert asyncio.run(provider.list()) == [entry]
    assert icloud.calls[calls:] == ["list"]


def test_lists_only_its_own_records(backups: Backups) -> None:
    icloud = FakeICloud()
    provider = ICloudProvider(icloud)
    entry = asyncio.run(provider.upload(backups.snapshot()))
    _, data = icloud.records[entry.id]
    icloud.records["notes"] = ("{}", b"")
    icloud.records["solaris-backup-20260101-000000-000000000000"] = ("not json", data)
    icloud.records["solaris-backup-20260101-000000-111111111111"] = ('{"device": 1}', data)

    async def scenario() -> None:
        assert [e.id for e in await provider.list()] == [entry.id]
        for name in ("notes", "../" + entry.id, entry.id + ".json"):
            with pytest.raises(BackupError) as raised:
                await provider.download(name)
            assert raised.value.code == "backup_missing"
            await provider.delete(name)
        assert "notes" in icloud.records  # not ours to delete

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (NOT_SIGNED_IN, "not_signed_in"),
        (QUOTA_EXCEEDED, "cloud_full"),
        (UNREACHABLE, "cloud_unreachable"),
        (TIMED_OUT, "cloud_unreachable"),
        (FAILED, "cloud_failed"),
    ],
)
def test_failures_become_backup_errors(backups: Backups, result: int, code: str) -> None:
    icloud = FakeICloud()
    icloud.failures["save"] = ManagerError(result, "Could not save the snapshot to iCloud")
    with pytest.raises(BackupError) as raised:
        asyncio.run(ICloudProvider(icloud).upload(backups.snapshot()))
    assert (raised.value.code, raised.value.message) == (
        code,
        "Could not save the snapshot to iCloud",
    )


def test_calls_block_on_daemon_threads(backups: Backups) -> None:
    """Never on the asyncio thread, and never one that would hold the app's exit up."""
    icloud = FakeICloud()
    provider = ICloudProvider(icloud)

    async def scenario() -> threading.Thread:
        entry = await provider.upload(backups.snapshot())
        await provider.download(entry.id)
        await provider.status()
        return threading.current_thread()

    loop_thread = asyncio.run(scenario())
    assert loop_thread not in icloud.threads
    assert all(thread.daemon for thread in icloud.threads)
