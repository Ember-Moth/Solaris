"""Import, update, use and remove profiles, their overrides, and restoring a backup
(docs/routing.md §5).

A profile reaches the store only after the host read it: ``inspect_source`` says whether it
brings rules, a profile with rules must pass ``validate`` as a configuration of its own, and
one that joins the node pool of the plan in use must leave a composition the host accepts.
The first import picks the routing mode (§5.1): the profile's own rules when it has some,
otherwise the default plan.

While no core can answer (macOS with the tunnel down, D27), nothing is read or validated: a
profile imported then is stored unread, and the tunnel part reads it at the next connect.

Switching profiles or updating the one in use reloads it in place, with the TUN and system
proxy state the host reports, so neither is interrupted.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from solaris.host.base import CompositionReport, SourceInspection, TunnelConfig
from solaris.models import plans
from solaris.models.library.core import has_rules
from solaris.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from solaris.models.routing import MODE_PLAN, MODE_PROFILE, Plan
from solaris.models.subscription import Fetcher, check_url, decode_config, read_config_file

if TYPE_CHECKING:
    from solaris.models.library.core import LibraryCore

DEFAULT_AUTO_UPDATE_HOURS = 12
# After a failed automatic update, wait this long before the next attempt.
RETRY_AFTER_S = 30 * 60


@dataclass(frozen=True)
class Removal:
    """What removing a profile does, for the confirmation shown before it."""

    profile: Profile
    active: bool  # its rules are the ones in use (订阅自带)
    successor: Profile | None  # whose rules are used afterwards, when ``active``
    pool_nodes: int | None  # nodes the pool of the plan in use loses; None when it loses none


class Profiles:
    def __init__(self, core: LibraryCore, fetcher: Fetcher) -> None:
        self._core = core
        self._fetcher = fetcher

    # -- import ------------------------------------------------------------------------------
    async def import_url(self, url: str, name: str = "", auto_update: bool = True) -> ProfileList:
        url = check_url(url)
        download = await self._fetcher.fetch(url)
        text = decode_config(download.body)
        profile = Profile(
            id=uuid.uuid4().hex,
            name=name.strip() or download.filename or urlsplit(url).hostname or url,
            kind=KIND_SUBSCRIPTION,
            url=url,
            auto_update_hours=DEFAULT_AUTO_UPDATE_HOURS if auto_update else 0,
            updated_at=self._core.clock(),
            usage=download.usage,
            proxy_count=count_proxies(text),
        )
        async with self._core.lock:
            return await self._add(profile, text)

    async def import_file(self, path: Path) -> ProfileList:
        text = await asyncio.to_thread(read_config_file, path)
        profile = Profile(
            id=uuid.uuid4().hex,
            name=path.stem,
            kind=KIND_FILE,
            path=str(path),
            updated_at=self._core.clock(),
            proxy_count=count_proxies(text),
        )
        async with self._core.lock:
            return await self._add(profile, text)

    async def _add(self, profile: Profile, text: str) -> ProfileList:
        core = self._core
        inspection = await core.read(text, None)
        if inspection is None:
            # Read at the next connect, which settles the rest (Tunnel._read_unread).
            await asyncio.to_thread(core.store.write_config, profile.id, text)
            await core.save(core.list.put(replace(profile, proxy_count=None, unread=True)))
            return core.list
        profile = replace(profile, has_rules=inspection.has_rules)
        if profile.proxy_count is None:
            profile = replace(profile, proxy_count=len(inspection.nodes))
        updated = core.list.put(profile)
        if updated.active is None and inspection.has_rules:
            updated = replace(updated, active=profile.id)
        mode = core.state.mode
        plan: Plan | None = None
        report: CompositionReport | None = None
        if mode is None:
            # The first import picks the routing (routing.md §5.1).
            mode = MODE_PROFILE if inspection.has_rules else MODE_PLAN
        if mode == MODE_PLAN:
            plan = core.current_plan() or plans.default_plan()
            report = await core.validate_plan(plan, updated, texts={profile.id: text})
        await asyncio.to_thread(core.store.write_config, profile.id, text)
        core.inspections[profile.id] = inspection
        await core.save(updated)
        core.report = report  # the pool has another profile
        if core.state.mode is None:
            await core.store_mode(mode, plan)
        elif mode == MODE_PLAN:
            await core.reload_if_running()
        return core.list

    # -- update ------------------------------------------------------------------------------
    async def update(self, profile_id: str) -> ProfileList:
        """Download a subscription again, or re-read a file, and reload it if it is in use."""
        core = self._core
        profile = core.list.get(profile_id)
        try:
            text, usage, inspection = await self._fetch_again(profile)
            async with core.lock:
                await self._commit_update(profile_id, text, usage, inspection)
        except Exception:
            core.mark_failed(profile_id, core.clock())
            raise
        core.mark_failed(profile_id, None)
        return core.list

    async def update_due(self) -> ProfileList:
        """Update every subscription whose interval has passed. A failure shows in
        ``ProfileList.update_failed`` and is retried later instead of raised. They wait while
        no core can read them."""
        core = self._core
        if not core.host.core_available:
            return core.list
        now = core.clock()
        for profile in [p for p in core.list.profiles if self._due(p, now)]:
            try:
                text, usage, inspection = await self._fetch_again(profile)
                async with core.lock:
                    await self._commit_update(profile.id, text, usage, inspection)
            except Exception:
                core.mark_failed(profile.id, now)
            else:
                core.mark_failed(profile.id, None)
        return core.list

    def _due(self, profile: Profile, now: float) -> bool:
        if not profile.is_subscription or profile.auto_update_hours <= 0:
            return False
        failed_at = self._core.failed.get(profile.id)
        if failed_at is not None and now - failed_at < RETRY_AFTER_S:
            return False
        return now - profile.updated_at >= profile.auto_update_hours * 3600

    async def _fetch_again(
        self, profile: Profile
    ) -> tuple[str, Usage | None, SourceInspection | None]:
        """The profile's new text, read by the host, or unread while no core can read it;
        downloads run outside the lock."""
        if profile.is_subscription:
            download = await self._fetcher.fetch(profile.url)
            text, usage = decode_config(download.body), download.usage
        else:
            text, usage = await asyncio.to_thread(read_config_file, Path(profile.path)), None
        return text, usage, await self._core.read(text, profile.id)

    async def _commit_update(
        self,
        profile_id: str,
        text: str,
        usage: Usage | None,
        inspection: SourceInspection | None,
        *,
        reload: bool = True,
    ) -> None:
        """Store a profile's new text; None for ``inspection`` stores it unread, keeping the
        text a core read last for when it refuses the new one at connect."""
        core = self._core
        # The profile may have been removed while it downloaded.
        profile = core.list.get(profile_id)
        if inspection is None:
            unread = replace(
                profile,
                updated_at=core.clock(),
                usage=usage,
                proxy_count=None,
                has_rules=None,
                unread=True,
            )
            await asyncio.to_thread(core.store.write_config, profile_id, text, keep_read=True)
            core.inspections.pop(profile_id, None)
            await core.save(core.list.put(unread))
            if profile.in_pool:
                core.report = None  # the pool lost its nodes until they are read
            return
        count = count_proxies(text)
        refreshed = replace(
            profile,
            updated_at=core.clock(),
            usage=usage,
            proxy_count=len(inspection.nodes) if count is None else count,
            has_rules=inspection.has_rules,
            unread=False,
        )
        updated = core.list.put(refreshed)
        pooled = core.state.mode == MODE_PLAN and profile.in_pool
        report = None
        if pooled:
            report = await core.validate_plan(
                core.current_plan(), updated, texts={profile_id: text}
            )
        await asyncio.to_thread(core.store.write_config, profile_id, text)
        core.inspections[profile_id] = inspection
        await core.save(updated)
        if profile.in_pool:
            core.report = report  # the pool has other nodes
        if not reload:
            return
        if pooled or (core.state.mode == MODE_PROFILE and profile_id == core.list.active):
            await core.reload_if_running()

    # -- the profile in use, and the pool ------------------------------------------------------
    async def activate(self, profile_id: str) -> ProfileList:
        """Route with this profile's own rules (订阅自带); a profile with nodes only cannot."""
        core = self._core
        async with core.lock:
            if profile_id == core.list.active and core.state.mode == MODE_PROFILE:
                return core.list
            profile = core.list.get(profile_id)
            await core.inspect_missing()
            if has_rules(profile, core.inspections) is False:
                raise ProfileError("no_rules", "this profile has nodes only")
            text = await asyncio.to_thread(core.store.read_config, profile_id)
            config = TunnelConfig(text, await core.overrides(profile_id))
            # Reload first: if the core rejects the profile, the old routing stays in use.
            await core.try_config(config)
            await core.save(replace(core.list, active=profile_id))
            if core.state.mode != MODE_PROFILE:
                await core.store_mode(MODE_PROFILE, None)
            return core.list

    async def set_in_pool(self, profile_id: str, in_pool: bool) -> ProfileList:
        """Let the profile's nodes join the pool of the user's plans, or keep them out."""
        core = self._core
        async with core.lock:
            profile = core.list.get(profile_id)
            if profile.in_pool == in_pool:
                return core.list
            updated = core.list.put(replace(profile, in_pool=in_pool))
            report = None
            if core.state.mode == MODE_PLAN:
                report = await core.validate_plan(core.current_plan(), updated)
            await core.save(updated)
            core.report = report
            if core.state.mode == MODE_PLAN:
                await core.reload_if_running()
            return core.list

    async def set_auto_update(self, profile_id: str, enabled: bool) -> ProfileList:
        core = self._core
        async with core.lock:
            hours = DEFAULT_AUTO_UPDATE_HOURS if enabled else 0
            profile = replace(core.list.get(profile_id), auto_update_hours=hours)
            await core.save(core.list.put(profile))
            return core.list

    # -- removal -----------------------------------------------------------------------------
    async def removal(self, profile_id: str) -> Removal:
        """What ``remove`` would do now."""
        core = self._core
        profile = core.list.get(profile_id)
        await core.inspect_missing()
        active = profile_id == core.list.active and core.state.mode == MODE_PROFILE
        successor = core.first_with_rules(core.list, exclude=profile_id) if active else None
        pool_nodes = None
        if core.state.mode == MODE_PLAN and profile.in_pool:
            inspection = core.inspections.get(profile_id)
            if inspection is not None:
                pool_nodes = sum(not node.info for node in inspection.nodes)
        return Removal(profile, active, successor, pool_nodes)

    async def remove(self, profile_id: str) -> ProfileList:
        """Remove a profile. When its rules are in use, the earliest profile with rules takes
        over, or the user's plan when none has any (routing.md §5.1); a running tunnel
        reloads first, and keeps the profile when the reload fails."""
        core = self._core
        async with core.lock:
            profile = core.list.get(profile_id)
            await core.inspect_missing()
            remaining = core.list.without(profile_id)
            successor = core.first_with_rules(core.list, exclude=profile_id)
            mode = core.state.mode
            plan: Plan | None = None
            report = core.report if not profile.in_pool else None
            if core.list.active == profile_id:
                remaining = replace(remaining, active=successor.id if successor else None)
                if mode == MODE_PROFILE and successor is None:
                    mode = MODE_PLAN
                    plan = core.current_plan() or plans.default_plan()
            if mode == MODE_PROFILE and remaining.active != core.list.active:
                assert remaining.active is not None
                text = await asyncio.to_thread(core.store.read_config, remaining.active)
                config = TunnelConfig(text, await core.overrides(remaining.active))
                await core.try_config(config)
            elif mode == MODE_PLAN and (plan is not None or profile.in_pool):
                composition = await core.composition(
                    plan or core.current_plan(), remaining, info=core.info_filter()
                )
                config = TunnelConfig(
                    overrides=await core.global_override(), composition=composition
                )
                report = await core.check(config)
                await core.reload_config_if_running(config)
            await core.save(remaining)
            core.report = report
            await asyncio.to_thread(core.store.delete_config, profile_id)
            core.inspections.pop(profile_id, None)
            core.mark_failed(profile_id, None)
            if mode is not None and mode != core.state.mode:
                await core.store_mode(mode, plan)
            return core.list

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    async def read_override(self, profile_id: str | None) -> str:
        if profile_id is not None:
            self._core.list.get(profile_id)
        return await asyncio.to_thread(self._core.store.read_override, profile_id)

    async def save_override(self, profile_id: str | None, text: str) -> ProfileList:
        """Store an override once the host accepts it, then reload the tunnel if the override
        applies to what it runs. The global override applies to both routing modes and is
        checked with the routing in use; a profile's own applies to its rules only, and is
        checked with them. Blank text removes the override."""
        core = self._core
        async with core.lock:
            reloads = True
            if profile_id is None:
                config = await core.current_config(global_text=text)
                if config is None:
                    raise ProfileError("no_profile", "import a profile first")
                await core.check(config)
            else:
                target = core.list.get(profile_id)
                await core.inspect_missing()
                reloads = core.state.mode == MODE_PROFILE and target.id == core.list.active
                if has_rules(target, core.inspections) is not False:
                    overrides = await core.overrides(target.id, own_text=text)
                    config_text = await asyncio.to_thread(core.store.read_config, target.id)
                    await core.check(TunnelConfig(config_text, overrides))
            await asyncio.to_thread(core.store.write_override, profile_id, text)
            present = bool(text.strip())
            if profile_id is None:
                core.list = replace(core.list, has_global_override=present)
            else:
                core.list = core.list.put(replace(core.list.get(profile_id), has_override=present))
            if reloads:
                await core.reload_if_running()
            return core.list

    # -- restoring a backup (docs/storage.md §3) ----------------------------------------------
    async def restore(self, write: Callable[[], object]) -> ProfileList:
        """Have ``write`` put a backup into the store, on a worker thread while no change of
        the library can come between, and take it up. Then the host reads every profile as
        an import does: subscriptions are downloaded again, a local file's config is the one
        restored. A profile that fails is kept and marked as a failed update. When the
        profile in use is gone, the earliest with rules takes over. A running tunnel then
        runs the restored routing; raises when it cannot, with everything else done."""
        core = self._core
        async with core.lock:
            await asyncio.to_thread(write)
            core.list = await asyncio.to_thread(core.store.load)
            core.state = await asyncio.to_thread(core.routing_store.state)
            core.plans = await asyncio.to_thread(core.routing_store.plans)
            await core.networks.take_restored()
            core.failed.clear()
            core.inspections.clear()
            core.report = None
        for profile in core.list.profiles:
            try:
                if profile.is_subscription:
                    text, usage, inspection = await self._fetch_again(profile)
                else:
                    text, usage = await asyncio.to_thread(core.store.read_config, profile.id), None
                    inspection = await core.read(text, profile.id)
                async with core.lock:
                    await self._commit_update(profile.id, text, usage, inspection, reload=False)
            except Exception:
                core.mark_failed(profile.id, core.clock())
        async with core.lock:
            if core.state.mode == MODE_PROFILE and core.list.active_profile() is None:
                successor = core.first_with_rules(core.list)
                if successor is not None:
                    await core.save(replace(core.list, active=successor.id))
            await core.reload_if_running()
            return core.list
