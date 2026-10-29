"""Import, update and switch profiles; keep the running tunnel on the active one.

A profile reaches the store only after the host validated it. Switching profiles or
updating the active one while the tunnel runs reloads it in place, with the TUN and
system proxy state the host reports, so neither is interrupted.

Every method runs on the asyncio thread and returns the new ProfileList. Downloads and
validation run outside the lock; writing the store and reloading hold it, so an automatic
update cannot interleave with a switch.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from mihomo_desktop.host.base import HostStatus, RunOptions, TunnelConfig, TunnelHost
from mihomo_desktop.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.models.subscription import (
    Fetcher,
    check_url,
    decode_config,
    read_config_file,
)

DEFAULT_AUTO_UPDATE_HOURS = 12
# After a failed automatic update, wait this long before the next attempt.
RETRY_AFTER_S = 30 * 60


def live_options(status: HostStatus) -> RunOptions:
    """Options that keep the tunnel as it runs. The port and LAN access come from the
    settings page (roadmap M3); until then the profile's own values apply."""
    return RunOptions(
        tun_enabled=status.tun_enabled, system_proxy_enabled=status.system_proxy_enabled
    )


class ProfileLibrary:
    def __init__(
        self,
        store: ProfileStore,
        host: TunnelHost,
        fetcher: Fetcher,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._host = host
        self._fetcher = fetcher
        self._clock = clock
        self._lock = asyncio.Lock()
        self._list = store.load()
        self._failed: dict[str, float] = {}  # profile id -> time of the failed update

    def snapshot(self) -> ProfileList:
        """The profiles as loaded; call before the asyncio thread starts using the library."""
        return self._list

    async def current(self) -> ProfileList:
        return self._list

    # -- import ------------------------------------------------------------------------------
    async def import_url(self, url: str, name: str = "", auto_update: bool = True) -> ProfileList:
        url = check_url(url)
        download = await self._fetcher.fetch(url)
        text = decode_config(download.body)
        await self._host.validate(TunnelConfig(text))
        profile = Profile(
            id=uuid.uuid4().hex,
            name=name.strip() or download.filename or urlsplit(url).hostname or url,
            kind=KIND_SUBSCRIPTION,
            url=url,
            auto_update_hours=DEFAULT_AUTO_UPDATE_HOURS if auto_update else 0,
            updated_at=self._clock(),
            usage=download.usage,
            proxy_count=count_proxies(text),
        )
        async with self._lock:
            return await self._add(profile, text)

    async def import_file(self, path: Path) -> ProfileList:
        text = await asyncio.to_thread(read_config_file, path)
        await self._host.validate(TunnelConfig(text))
        profile = Profile(
            id=uuid.uuid4().hex,
            name=path.stem,
            kind=KIND_FILE,
            path=str(path),
            updated_at=self._clock(),
            proxy_count=count_proxies(text),
        )
        async with self._lock:
            return await self._add(profile, text)

    async def _add(self, profile: Profile, text: str) -> ProfileList:
        updated = self._list.put(profile)
        if updated.active is None:
            updated = replace(updated, active=profile.id)
        await asyncio.to_thread(self._store.write_config, profile.id, text)
        await self._save(updated)
        return updated

    # -- update ------------------------------------------------------------------------------
    async def update(self, profile_id: str) -> ProfileList:
        """Download a subscription again, or re-read a file, and reload it if it is in use."""
        profile = self._list.get(profile_id)
        try:
            text, usage = await self._fetch_again(profile)
            async with self._lock:
                await self._commit_update(profile_id, text, usage)
        except Exception:
            self._mark_failed(profile_id, self._clock())
            raise
        self._mark_failed(profile_id, None)
        return self._list

    async def update_due(self) -> ProfileList:
        """Update every subscription whose interval has passed. A failure shows in
        ``ProfileList.update_failed`` and is retried later instead of raised."""
        now = self._clock()
        for profile in [p for p in self._list.profiles if self._due(p, now)]:
            try:
                text, usage = await self._fetch_again(profile)
                async with self._lock:
                    await self._commit_update(profile.id, text, usage)
            except Exception:
                self._mark_failed(profile.id, now)
            else:
                self._mark_failed(profile.id, None)
        return self._list

    def _due(self, profile: Profile, now: float) -> bool:
        if not profile.is_subscription or profile.auto_update_hours <= 0:
            return False
        failed_at = self._failed.get(profile.id)
        if failed_at is not None and now - failed_at < RETRY_AFTER_S:
            return False
        return now - profile.updated_at >= profile.auto_update_hours * 3600

    async def _fetch_again(self, profile: Profile) -> tuple[str, Usage | None]:
        """The profile's new text, validated; downloads run outside the lock."""
        if profile.is_subscription:
            download = await self._fetcher.fetch(profile.url)
            text, usage = decode_config(download.body), download.usage
        else:
            text, usage = await asyncio.to_thread(read_config_file, Path(profile.path)), None
        await self._host.validate(TunnelConfig(text))
        return text, usage

    async def _commit_update(self, profile_id: str, text: str, usage: Usage | None) -> None:
        # The profile may have been removed while it downloaded.
        profile = self._list.get(profile_id)
        refreshed = replace(
            profile, updated_at=self._clock(), usage=usage, proxy_count=count_proxies(text)
        )
        await asyncio.to_thread(self._store.write_config, profile_id, text)
        await self._save(self._list.put(refreshed))
        if profile_id == self._list.active:
            await self._reload_if_running(text)

    def _mark_failed(self, profile_id: str, failed_at: float | None) -> None:
        if failed_at is None:
            self._failed.pop(profile_id, None)
        else:
            self._failed[profile_id] = failed_at
        self._list = replace(self._list, update_failed=frozenset(self._failed))

    # -- switch and edit ---------------------------------------------------------------------
    async def activate(self, profile_id: str) -> ProfileList:
        async with self._lock:
            if profile_id == self._list.active:
                return self._list
            self._list.get(profile_id)
            text = await asyncio.to_thread(self._store.read_config, profile_id)
            # Reload first: if the core rejects the profile, the old one stays in use.
            await self._reload_if_running(text)
            await self._save(replace(self._list, active=profile_id))
            return self._list

    async def set_auto_update(self, profile_id: str, enabled: bool) -> ProfileList:
        async with self._lock:
            hours = DEFAULT_AUTO_UPDATE_HOURS if enabled else 0
            profile = replace(self._list.get(profile_id), auto_update_hours=hours)
            await self._save(self._list.put(profile))
            return self._list

    async def remove(self, profile_id: str) -> ProfileList:
        async with self._lock:
            self._list.get(profile_id)
            if profile_id == self._list.active and await self._running() is not None:
                raise ProfileError("profile_in_use", "disconnect before removing this profile")
            await self._save(self._list.without(profile_id))
            await asyncio.to_thread(self._store.delete_config, profile_id)
            self._mark_failed(profile_id, None)
            return self._list

    # -- tunnel ------------------------------------------------------------------------------
    async def start_tunnel(self, options: RunOptions) -> None:
        async with self._lock:
            active = self._list.active_profile()
            if active is None:
                raise ProfileError("no_profile", "import a profile first")
            text = await asyncio.to_thread(self._store.read_config, active.id)
            await self._host.start(TunnelConfig(text, options=options))

    async def _running(self) -> HostStatus | None:
        status = await self._host.get_status()
        return status if status.state == "running" else None

    async def _reload_if_running(self, text: str) -> None:
        status = await self._running()
        if status is not None:
            await self._host.reload(TunnelConfig(text, options=live_options(status)))

    async def _save(self, profiles: ProfileList) -> None:
        await asyncio.to_thread(self._store.save, profiles)
        # Failures may have been marked while the index was written.
        self._list = replace(profiles, update_failed=frozenset(self._failed))
