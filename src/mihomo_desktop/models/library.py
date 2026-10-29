"""Import, update and switch profiles; keep the running tunnel on the active one.

A profile reaches the store only after the host validated it. Switching profiles or
updating the active one while the tunnel runs reloads it in place, with the TUN and
system proxy state the host reports, so neither is interrupted.

Every config goes to the host with the overrides that apply to it (docs/architecture.md
§7.4): the global one, then the profile's own. An override is stored only after the host
accepted it on the profile it affects. The run options come from the settings (§7.8): a
start asks for the TUN and system proxy the user chose, a reload keeps the ones the tunnel
runs with, and both use the settings' mixed port and LAN access.

Every method runs on the asyncio thread and returns the new ProfileList. Downloads and
their validation run outside the lock; writing the store and reloading hold it, so an
automatic update cannot interleave with a switch. Saving an override validates under the
lock as well, so the other override it is checked with cannot change meanwhile.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from mihomo_desktop.host.base import HostError, HostStatus, RunOptions, TunnelConfig, TunnelHost
from mihomo_desktop.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from mihomo_desktop.models.settings import Settings
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


@dataclass(frozen=True)
class Removal:
    """What removing a profile does, for the confirmation shown before it."""

    profile: Profile
    active: bool  # the profile is the one in use (the tunnel is stopped)
    successor: Profile | None  # the profile in use afterwards, when ``active``


def live_options(status: HostStatus, settings: Settings) -> RunOptions:
    """Options that keep the TUN and system proxy as the tunnel runs them."""
    return replace(
        settings.run_options(),
        tun_enabled=status.tun_enabled,
        system_proxy_enabled=status.system_proxy_enabled,
    )


@contextmanager
def _naming_switches(options: RunOptions) -> Iterator[None]:
    """A host that never creates a TUN or sets the system proxy, the development service
    (docs/architecture.md §11), refuses them with ``unsupported_method``; say which switch."""
    try:
        yield
    except HostError as error:
        if error.code != "unsupported_method":
            raise
        if options.tun_enabled and options.system_proxy_enabled:
            code = "tun_and_system_proxy_unsupported"
        elif options.tun_enabled:
            code = "tun_unsupported"
        elif options.system_proxy_enabled:
            code = "system_proxy_unsupported"
        else:
            raise
        raise ProfileError(code, error.message) from error


class ProfileLibrary:
    def __init__(
        self,
        store: ProfileStore,
        host: TunnelHost,
        fetcher: Fetcher,
        clock: Callable[[], float] = time.time,
        settings: Callable[[], Settings] = Settings,
    ) -> None:
        """``settings`` returns the current settings; it is read on the asyncio thread."""
        self._store = store
        self._host = host
        self._fetcher = fetcher
        self._clock = clock
        self._settings = settings
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
        await self._host.validate(TunnelConfig(text, await self._overrides(None)))
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
        await self._host.validate(TunnelConfig(text, await self._overrides(None)))
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
        await self._host.validate(TunnelConfig(text, await self._overrides(profile.id)))
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
            await self._reload_if_running(profile_id, text)

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
            await self._reload_if_running(profile_id, text)
            await self._save(replace(self._list, active=profile_id))
            return self._list

    async def set_auto_update(self, profile_id: str, enabled: bool) -> ProfileList:
        async with self._lock:
            hours = DEFAULT_AUTO_UPDATE_HOURS if enabled else 0
            profile = replace(self._list.get(profile_id), auto_update_hours=hours)
            await self._save(self._list.put(profile))
            return self._list

    async def removal(self, profile_id: str) -> Removal:
        """What ``remove`` would do now; raises as it would when the profile cannot go."""
        profile = self._list.get(profile_id)
        if profile_id != self._list.active:
            return Removal(profile, active=False, successor=None)
        await self._check_not_running()
        successor = self._list.without(profile_id).active_profile()
        return Removal(profile, active=True, successor=successor)

    async def remove(self, profile_id: str) -> ProfileList:
        async with self._lock:
            self._list.get(profile_id)
            if profile_id == self._list.active:
                await self._check_not_running()
            await self._save(self._list.without(profile_id))
            await asyncio.to_thread(self._store.delete_config, profile_id)
            self._mark_failed(profile_id, None)
            return self._list

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    async def read_override(self, profile_id: str | None) -> str:
        if profile_id is not None:
            self._list.get(profile_id)
        return await asyncio.to_thread(self._store.read_override, profile_id)

    async def save_override(self, profile_id: str | None, text: str) -> ProfileList:
        """Store an override once the host accepts it, then reload the tunnel if it runs the
        profile affected. The global override is checked with the active profile, a profile's
        own with that profile. Blank text removes the override."""
        async with self._lock:
            if profile_id is None:
                target = self._list.active_profile()
                if target is None:
                    raise ProfileError("no_profile", "import a profile first")
                overrides = await self._overrides(target.id, global_text=text)
            else:
                target = self._list.get(profile_id)
                overrides = await self._overrides(target.id, own_text=text)
            config = await asyncio.to_thread(self._store.read_config, target.id)
            await self._host.validate(TunnelConfig(config, overrides))
            await asyncio.to_thread(self._store.write_override, profile_id, text)
            present = bool(text.strip())
            if profile_id is None:
                self._list = replace(self._list, has_global_override=present)
            else:
                self._list = self._list.put(replace(target, has_override=present))
            if target.id == self._list.active:
                await self._reload_if_running(target.id, config)
            return self._list

    # -- tunnel ------------------------------------------------------------------------------
    async def start_tunnel(self) -> None:
        """Start the active profile with the settings' run options."""
        async with self._lock:
            active = self._list.active_profile()
            if active is None:
                raise ProfileError("no_profile", "import a profile first")
            text = await asyncio.to_thread(self._store.read_config, active.id)
            options = self._settings().run_options()
            config = TunnelConfig(text, await self._overrides(active.id), options)
            with _naming_switches(options):
                await self._host.start(config)

    async def set_tun(self, enabled: bool) -> None:
        """Reload the running tunnel with TUN on or off; a stopped one starts with the
        setting, so there is nothing to do."""
        async with self._lock:
            status = await self._running()
            if status is not None:
                options = replace(live_options(status, self._settings()), tun_enabled=enabled)
                await self._reload_active(options)

    async def set_system_proxy(self, enabled: bool) -> None:
        async with self._lock:
            if await self._running() is None:
                return
            with _naming_switches(RunOptions(system_proxy_enabled=enabled)):
                await self._host.set_system_proxy(enabled)

    async def apply_settings(self, settings: Settings) -> None:
        """Reload the running tunnel with the mixed port and LAN access of ``settings``,
        before they are stored: if the core refuses them, the old ones stay in use."""
        async with self._lock:
            status = await self._running()
            if status is not None:
                await self._reload_active(live_options(status, settings))

    async def _reload_active(self, options: RunOptions) -> None:
        active = self._list.active_profile()
        if active is None:
            raise ProfileError("no_profile", "import a profile first")
        text = await asyncio.to_thread(self._store.read_config, active.id)
        config = TunnelConfig(text, await self._overrides(active.id), options)
        with _naming_switches(options):
            await self._host.reload(config)

    async def _running(self) -> HostStatus | None:
        status = await self._host.get_status()
        return status if status.state == "running" else None

    async def _check_not_running(self) -> None:
        """The profile in use can only go while the tunnel is stopped."""
        if await self._running() is not None:
            raise ProfileError("profile_in_use", "disconnect before removing this profile")

    async def _reload_if_running(self, profile_id: str, text: str) -> None:
        status = await self._running()
        if status is not None:
            overrides = await self._overrides(profile_id)
            options = live_options(status, self._settings())
            await self._host.reload(TunnelConfig(text, overrides, options))

    async def _overrides(
        self,
        profile_id: str | None,
        *,
        global_text: str | None = None,
        own_text: str | None = None,
    ) -> tuple[str, ...]:
        """The override texts that apply to a stored profile, or to a new one for None: the
        global override, then the profile's own. A text passed in stands for the stored one."""
        if global_text is None:
            global_text = await asyncio.to_thread(self._store.read_override, None)
        if own_text is None:
            own_text = (
                await asyncio.to_thread(self._store.read_override, profile_id)
                if profile_id is not None
                else ""
            )
        return tuple(text for text in (global_text, own_text) if text.strip())

    async def _save(self, profiles: ProfileList) -> None:
        await asyncio.to_thread(self._store.save, profiles)
        # Failures may have been marked while the index was written.
        self._list = replace(profiles, update_failed=frozenset(self._failed))
