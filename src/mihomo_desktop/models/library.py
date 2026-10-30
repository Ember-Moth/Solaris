"""Import, update and remove profiles, edit the user's plans, and keep the running tunnel on
the routing the user chose (docs/routing.md §5).

The routing mode decides what the tunnel runs:

    订阅自带 (MODE_PROFILE)  the active profile's own YAML, with the global override and the
                             profile's own (routing.md §5.6)
    我的方案 (MODE_PLAN)     a composition: the profiles in the node pool, in list order, and
                             the plan in use; only the global override applies

A profile reaches the store only after the host read it: ``inspect_source`` says whether it
brings rules, a profile with rules must pass ``validate`` as a configuration of its own, and
one that joins the node pool of the plan in use must leave a composition the host accepts.
The first import picks the routing mode (§5.1): the profile's own rules when it has some,
otherwise the default plan.

Every change to the routing is validated, then stored, then reloaded into a running tunnel
(§5.4). Switching profiles or updating the one in use reloads it in place, with the TUN and
system proxy state the host reports, so neither is interrupted.

Every method runs on the asyncio thread. Downloads and their validation run outside the
lock; writing the store and reloading hold it, so an automatic update cannot interleave
with a switch or a plan edit.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from mihomo_desktop.host.base import (
    Composition,
    CompositionReport,
    CompositionSource,
    GeoCategories,
    HostError,
    HostStatus,
    ProxyGroup,
    Rule,
    RunOptions,
    SourceInspection,
    TunnelConfig,
    TunnelHost,
    bundled_geo_categories,
)
from mihomo_desktop.models import plans
from mihomo_desktop.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
    count_proxies,
)
from mihomo_desktop.models.routing import (
    DEFAULT_INFO_PATTERN,
    MODE_PLAN,
    MODE_PROFILE,
    MODES,
    Plan,
    RoutingState,
    RoutingStore,
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
    active: bool  # its rules are the ones in use (订阅自带)
    successor: Profile | None  # whose rules are used afterwards, when ``active``
    pool_nodes: int | None  # nodes the pool of the plan in use loses; None when it loses none


@dataclass(frozen=True)
class Routing:
    """The routing as the routing page shows it."""

    state: RoutingState  # profile_id is ProfileList.active
    plans: tuple[Plan, ...]
    # What the core made of the plan in use at its last validate, start or reload; None
    # until it has been composed with the current pool.
    report: CompositionReport | None
    inspections: Mapping[str, SourceInspection]  # by profile id; missing until read

    @property
    def mode(self) -> str | None:
        return self.state.mode

    @property
    def info_pattern(self) -> str:
        pattern = self.state.info_pattern
        return DEFAULT_INFO_PATTERN if pattern is None else pattern

    def plan(self) -> Plan | None:
        """The plan in use, or the first one when none was chosen."""
        return next((p for p in self.plans if p.id == self.state.plan_id), None) or next(
            iter(self.plans), None
        )

    def has_rules(self, profile_id: str) -> bool | None:
        """Whether the profile brings rules; None while it has not been read."""
        inspection = self.inspections.get(profile_id)
        return None if inspection is None else inspection.has_rules


@dataclass(frozen=True)
class ProfileRules:
    """The groups and rules of the profile in use, as the running core reports them."""

    groups: tuple[ProxyGroup, ...]
    rules: tuple[Rule, ...]


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
        locale: Callable[[], str] = plans.ui_locale,
    ) -> None:
        """``settings`` returns the current settings; it is read on the asyncio thread.
        ``locale`` names the groups the core generates for a plan (routing.md §8.5)."""
        self._store = store
        self._routing_store = RoutingStore(store.database)
        self._host = host
        self._fetcher = fetcher
        self._clock = clock
        self._settings = settings
        self._locale = locale
        self._lock = asyncio.Lock()
        self._list = store.load()
        self._failed: dict[str, float] = {}  # profile id -> time of the failed update
        self._inspections: dict[str, SourceInspection] = {}
        self._report: CompositionReport | None = None
        # None until the first import picks the mode (routing.md §5.1).
        self._state = self._routing_store.state()
        self._plans = self._routing_store.plans()

    def snapshot(self) -> ProfileList:
        """The profiles as loaded; call before the asyncio thread starts using the library."""
        return self._list

    def routing_snapshot(self) -> Routing:
        """The routing as loaded; call before the asyncio thread starts using the library."""
        return self._routing()

    async def current(self) -> ProfileList:
        return self._list

    async def routing(self) -> Routing:
        return self._routing()

    def _routing(self) -> Routing:
        return Routing(
            replace(self._state, profile_id=self._list.active),
            self._plans,
            self._report,
            dict(self._inspections),
        )

    # -- reading profiles as the core does ---------------------------------------------------
    def _info_filter(self) -> str:
        pattern = self._state.info_pattern
        return DEFAULT_INFO_PATTERN if pattern is None else pattern

    async def _inspect(self, text: str) -> SourceInspection:
        return await self._host.inspect_source(text, self._info_filter())

    async def inspect_all(self) -> Routing:
        """Read the profiles not read yet, so the routing page knows which bring rules. A
        profile the host cannot read stays unknown."""
        await self._inspect_missing()
        return self._routing()

    async def _inspect_missing(self) -> None:
        for profile in self._list.profiles:
            if profile.id in self._inspections:
                continue
            try:
                text = await asyncio.to_thread(self._store.read_config, profile.id)
                self._inspections[profile.id] = await self._inspect(text)
            except HostError, ProfileError:
                continue

    def _first_with_rules(self, profiles: ProfileList, exclude: str = "") -> Profile | None:
        """The earliest imported profile with rules of its own (routing.md §5.1)."""
        return next(
            (
                p
                for p in profiles.profiles
                if p.id != exclude and self._inspections.get(p.id, _UNKNOWN).has_rules
            ),
            None,
        )

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
            updated_at=self._clock(),
            usage=download.usage,
            proxy_count=count_proxies(text),
        )
        async with self._lock:
            return await self._add(profile, text)

    async def import_file(self, path: Path) -> ProfileList:
        text = await asyncio.to_thread(read_config_file, path)
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
        inspection = await self._inspect(text)
        if inspection.has_rules:
            await self._host.validate(TunnelConfig(text, await self._overrides(None)))
        if profile.proxy_count is None:
            profile = replace(profile, proxy_count=len(inspection.nodes))
        updated = self._list.put(profile)
        if updated.active is None and inspection.has_rules:
            updated = replace(updated, active=profile.id)
        mode = self._state.mode
        plan: Plan | None = None
        report: CompositionReport | None = None
        if mode is None:
            # The first import picks the routing (routing.md §5.1).
            mode = MODE_PROFILE if inspection.has_rules else MODE_PLAN
        if mode == MODE_PLAN:
            plan = self._current_plan() or plans.default_plan()
            report = await self._validate_plan(plan, updated, texts={profile.id: text})
        await asyncio.to_thread(self._store.write_config, profile.id, text)
        self._inspections[profile.id] = inspection
        await self._save(updated)
        self._report = report  # the pool has another profile
        if self._state.mode is None:
            await self._store_mode(mode, plan)
        elif mode == MODE_PLAN:
            await self._reload_if_running()
        return self._list

    async def _store_mode(self, mode: str, plan: Plan | None) -> None:
        if plan is not None and plan.id not in {p.id for p in self._plans}:
            await asyncio.to_thread(self._routing_store.put_plan, plan)
            self._plans = await asyncio.to_thread(self._routing_store.plans)
        changes: dict[str, str] = {"mode": mode}
        if plan is not None:
            changes["plan_id"] = plan.id
        self._state = await asyncio.to_thread(self._routing_store.update_state, **changes)

    # -- update ------------------------------------------------------------------------------
    async def update(self, profile_id: str) -> ProfileList:
        """Download a subscription again, or re-read a file, and reload it if it is in use."""
        profile = self._list.get(profile_id)
        try:
            text, usage, inspection = await self._fetch_again(profile)
            async with self._lock:
                await self._commit_update(profile_id, text, usage, inspection)
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
                text, usage, inspection = await self._fetch_again(profile)
                async with self._lock:
                    await self._commit_update(profile.id, text, usage, inspection)
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

    async def _fetch_again(self, profile: Profile) -> tuple[str, Usage | None, SourceInspection]:
        """The profile's new text, read by the host; downloads run outside the lock."""
        if profile.is_subscription:
            download = await self._fetcher.fetch(profile.url)
            text, usage = decode_config(download.body), download.usage
        else:
            text, usage = await asyncio.to_thread(read_config_file, Path(profile.path)), None
        inspection = await self._inspect(text)
        if inspection.has_rules:
            await self._host.validate(TunnelConfig(text, await self._overrides(profile.id)))
        return text, usage, inspection

    async def _commit_update(
        self, profile_id: str, text: str, usage: Usage | None, inspection: SourceInspection
    ) -> None:
        # The profile may have been removed while it downloaded.
        profile = self._list.get(profile_id)
        count = count_proxies(text)
        refreshed = replace(
            profile,
            updated_at=self._clock(),
            usage=usage,
            proxy_count=len(inspection.nodes) if count is None else count,
        )
        updated = self._list.put(refreshed)
        pooled = self._state.mode == MODE_PLAN and profile.in_pool
        report = None
        if pooled:
            report = await self._validate_plan(
                self._current_plan(), updated, texts={profile_id: text}
            )
        await asyncio.to_thread(self._store.write_config, profile_id, text)
        self._inspections[profile_id] = inspection
        await self._save(updated)
        if profile.in_pool:
            self._report = report  # the pool has other nodes
        if pooled or (self._state.mode == MODE_PROFILE and profile_id == self._list.active):
            await self._reload_if_running()

    def _mark_failed(self, profile_id: str, failed_at: float | None) -> None:
        if failed_at is None:
            self._failed.pop(profile_id, None)
        else:
            self._failed[profile_id] = failed_at
        self._list = replace(self._list, update_failed=frozenset(self._failed))

    # -- the profile in use, and the pool ------------------------------------------------------
    async def activate(self, profile_id: str) -> ProfileList:
        """Route with this profile's own rules (订阅自带); a profile with nodes only cannot."""
        async with self._lock:
            if profile_id == self._list.active and self._state.mode == MODE_PROFILE:
                return self._list
            self._list.get(profile_id)
            await self._inspect_missing()
            if self._inspections.get(profile_id, _UNKNOWN).has_rules is False:
                raise ProfileError("no_rules", "this profile has nodes only")
            text = await asyncio.to_thread(self._store.read_config, profile_id)
            config = TunnelConfig(text, await self._overrides(profile_id))
            # Reload first: if the core rejects the profile, the old routing stays in use.
            await self._try_config(config)
            await self._save(replace(self._list, active=profile_id))
            if self._state.mode != MODE_PROFILE:
                await self._store_mode(MODE_PROFILE, None)
            return self._list

    async def set_in_pool(self, profile_id: str, in_pool: bool) -> ProfileList:
        """Let the profile's nodes join the pool of the user's plans, or keep them out."""
        async with self._lock:
            profile = self._list.get(profile_id)
            if profile.in_pool == in_pool:
                return self._list
            updated = self._list.put(replace(profile, in_pool=in_pool))
            report = None
            if self._state.mode == MODE_PLAN:
                report = await self._validate_plan(self._current_plan(), updated)
            await self._save(updated)
            self._report = report
            if self._state.mode == MODE_PLAN:
                await self._reload_if_running()
            return self._list

    async def set_auto_update(self, profile_id: str, enabled: bool) -> ProfileList:
        async with self._lock:
            hours = DEFAULT_AUTO_UPDATE_HOURS if enabled else 0
            profile = replace(self._list.get(profile_id), auto_update_hours=hours)
            await self._save(self._list.put(profile))
            return self._list

    # -- removal -----------------------------------------------------------------------------
    async def removal(self, profile_id: str) -> Removal:
        """What ``remove`` would do now."""
        profile = self._list.get(profile_id)
        await self._inspect_missing()
        active = profile_id == self._list.active and self._state.mode == MODE_PROFILE
        successor = self._first_with_rules(self._list, exclude=profile_id) if active else None
        pool_nodes = None
        if self._state.mode == MODE_PLAN and profile.in_pool:
            inspection = self._inspections.get(profile_id)
            if inspection is not None:
                pool_nodes = sum(not node.info for node in inspection.nodes)
        return Removal(profile, active, successor, pool_nodes)

    async def remove(self, profile_id: str) -> ProfileList:
        """Remove a profile. When its rules are in use, the earliest profile with rules takes
        over, or the user's plan when none has any (routing.md §5.1); a running tunnel
        reloads first, and keeps the profile when the reload fails."""
        async with self._lock:
            profile = self._list.get(profile_id)
            await self._inspect_missing()
            remaining = self._list.without(profile_id)
            successor = self._first_with_rules(self._list, exclude=profile_id)
            mode = self._state.mode
            plan: Plan | None = None
            report = self._report if not profile.in_pool else None
            if self._list.active == profile_id:
                remaining = replace(remaining, active=successor.id if successor else None)
                if mode == MODE_PROFILE and successor is None:
                    mode = MODE_PLAN
                    plan = self._current_plan() or plans.default_plan()
            if mode == MODE_PROFILE and remaining.active != self._list.active:
                assert remaining.active is not None
                text = await asyncio.to_thread(self._store.read_config, remaining.active)
                config = TunnelConfig(text, await self._overrides(remaining.active))
                await self._try_config(config)
            elif mode == MODE_PLAN and (plan is not None or profile.in_pool):
                composition = await self._composition(
                    plan or self._current_plan(), remaining, info=self._info_filter()
                )
                config = TunnelConfig(
                    overrides=await self._global_override(), composition=composition
                )
                report = await self._host.validate(config)
                await self._reload_config_if_running(config)
            await self._save(remaining)
            self._report = report
            await asyncio.to_thread(self._store.delete_config, profile_id)
            self._inspections.pop(profile_id, None)
            self._mark_failed(profile_id, None)
            if mode is not None and mode != self._state.mode:
                await self._store_mode(mode, plan)
            return self._list

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    async def read_override(self, profile_id: str | None) -> str:
        if profile_id is not None:
            self._list.get(profile_id)
        return await asyncio.to_thread(self._store.read_override, profile_id)

    async def save_override(self, profile_id: str | None, text: str) -> ProfileList:
        """Store an override once the host accepts it, then reload the tunnel if the override
        applies to what it runs. The global override applies to both routing modes and is
        checked with the routing in use; a profile's own applies to its rules only, and is
        checked with them. Blank text removes the override."""
        async with self._lock:
            reloads = True
            if profile_id is None:
                config = await self._current_config(global_text=text)
                if config is None:
                    raise ProfileError("no_profile", "import a profile first")
                await self._host.validate(config)
            else:
                target = self._list.get(profile_id)
                await self._inspect_missing()
                reloads = self._state.mode == MODE_PROFILE and target.id == self._list.active
                if self._inspections.get(target.id, _UNKNOWN).has_rules is not False:
                    overrides = await self._overrides(target.id, own_text=text)
                    config_text = await asyncio.to_thread(self._store.read_config, target.id)
                    await self._host.validate(TunnelConfig(config_text, overrides))
            await asyncio.to_thread(self._store.write_override, profile_id, text)
            present = bool(text.strip())
            if profile_id is None:
                self._list = replace(self._list, has_global_override=present)
            else:
                self._list = self._list.put(
                    replace(self._list.get(profile_id), has_override=present)
                )
            if reloads:
                await self._reload_if_running()
            return self._list

    # -- routing: the mode, the plans, the pool settings --------------------------------------
    async def set_mode(self, mode: str) -> Routing:
        """Route with the active profile's rules or with the plan in use. The first switch to
        plans makes the default plan; switching to a profile's rules needs a profile with
        some, the active one or else the earliest."""
        if mode not in MODES:
            raise ValueError(f"invalid mode: {mode!r}")
        async with self._lock:
            if mode == self._state.mode:
                return self._routing()
            if mode == MODE_PROFILE:
                await self._inspect_missing()
                active = self._list.active_profile()
                if active is None or self._inspections.get(active.id, _UNKNOWN).has_rules is False:
                    active = self._first_with_rules(self._list)
                if active is None:
                    raise ProfileError("no_rules", "no profile has rules of its own")
                text = await asyncio.to_thread(self._store.read_config, active.id)
                config = TunnelConfig(text, await self._overrides(active.id))
                await self._try_config(config)
                if active.id != self._list.active:
                    await self._save(replace(self._list, active=active.id))
                await self._store_mode(MODE_PROFILE, None)
            else:
                plan = self._current_plan() or plans.default_plan()
                await self._apply_plan(plan, current=True, routing=True)
                await self._store_mode(MODE_PLAN, plan)
            return self._routing()

    async def select_plan(self, plan_id: str) -> Routing:
        async with self._lock:
            plan = next(p for p in self._plans if p.id == plan_id)
            if plan.id != self._state.plan_id:
                await self._apply_plan(plan, current=True)
                self._state = await asyncio.to_thread(
                    self._routing_store.update_state, plan_id=plan.id
                )
            return self._routing()

    async def put_plan(self, plan: Plan) -> Routing:
        """Store a plan once the host accepts it, and reload a running tunnel that routes
        with it. A new plan comes last; putting an earlier version back undoes a change."""
        async with self._lock:
            await self._apply_plan(plan)
            return self._routing()

    async def create_plan(self, name: str, base: str | None) -> Routing:
        """A new plan from the template, or a copy of the plan ``base``; it comes into use."""
        async with self._lock:
            if base is None:
                plan = plans.default_plan(name=name)
            else:
                plan = plans.copy_plan(next(p for p in self._plans if p.id == base), name)
            await self._apply_plan(plan, current=True)
            self._state = await asyncio.to_thread(self._routing_store.update_state, plan_id=plan.id)
            return self._routing()

    async def delete_plan(self, plan_id: str) -> Routing:
        """Delete a plan; the last one stays. The next plan takes over the one in use."""
        async with self._lock:
            if len(self._plans) <= 1:
                raise ProfileError("last_plan", "the last plan cannot be deleted")
            current = self._current_plan()
            if current is not None and current.id == plan_id:
                successor = next(p for p in self._plans if p.id != plan_id)
                await self._apply_plan(successor, current=True)
                self._state = await asyncio.to_thread(
                    self._routing_store.update_state, plan_id=successor.id
                )
            await asyncio.to_thread(self._routing_store.delete_plan, plan_id)
            self._plans = await asyncio.to_thread(self._routing_store.plans)
            return self._routing()

    async def set_info_pattern(self, pattern: str | None) -> Routing:
        """The expression that keeps information nodes out of the pool; None restores the
        built-in one. A pattern the core cannot use is refused."""
        async with self._lock:
            info = DEFAULT_INFO_PATTERN if pattern is None else pattern
            plan = self._current_plan()
            composition = await self._composition(plan, self._list, info=info)
            report = await self._host.validate(
                TunnelConfig(overrides=await self._global_override(), composition=composition)
            )
            self._state = await asyncio.to_thread(
                self._routing_store.update_state, info_pattern=pattern
            )
            self._inspections.clear()  # which nodes are information nodes changed
            await self._inspect_missing()
            self._report = report if plan is not None else None
            if self._state.mode == MODE_PLAN:
                await self._reload_if_running()
            return self._routing()

    async def compose_report(self) -> Routing:
        """Compose the plan in use with the current pool when that has not happened yet, for
        the routing page: the regions with nodes, the warnings of its rules."""
        async with self._lock:
            plan = self._current_plan()
            if self._report is None and plan is not None:
                self._report = await self._validate_plan(plan, self._list)
            return self._routing()

    async def profile_rules(self, profile_id: str) -> ProfileRules | None:
        """The groups and rules the core runs when it runs this profile's own rules; None
        when it runs something else, or nothing."""
        if self._state.mode != MODE_PROFILE or profile_id != self._list.active:
            return None
        if await self._running() is None:
            return None
        groups = await self._host.list_groups()
        rules = await self._host.list_rules()
        return ProfileRules(tuple(groups), tuple(rules))

    async def geo_categories(self) -> GeoCategories:
        """The categories the add-rule dialog lists: the core's, or the installer's list
        while the core cannot be asked."""
        try:
            return await self._host.list_geo_categories()
        except HostError:
            return bundled_geo_categories()

    def _current_plan(self) -> Plan | None:
        return self._routing().plan()

    async def _apply_plan(
        self, plan: Plan, *, current: bool | None = None, routing: bool | None = None
    ) -> None:
        """Validate a plan, store it, and reload a tunnel that routes with it. ``current``:
        the plan is, or becomes, the plan in use; ``routing``: the tunnel routes with it, or
        is about to. By default both follow the routing state."""
        if current is None:
            chosen = self._current_plan()
            current = chosen is None or chosen.id == plan.id
        if routing is None:
            routing = current and self._state.mode == MODE_PLAN
        composition = await self._composition(plan, self._list, info=self._info_filter())
        config = TunnelConfig(overrides=await self._global_override(), composition=composition)
        report = await self._host.validate(config)
        await asyncio.to_thread(self._routing_store.put_plan, plan)
        self._plans = await asyncio.to_thread(self._routing_store.plans)
        if current:
            self._report = report
        if routing:
            await self._reload_config_if_running(config)

    async def _validate_plan(
        self,
        plan: Plan | None,
        profiles: ProfileList,
        *,
        texts: Mapping[str, str] | None = None,
    ) -> CompositionReport | None:
        composition = await self._composition(plan, profiles, texts=texts, info=self._info_filter())
        return await self._host.validate(
            TunnelConfig(overrides=await self._global_override(), composition=composition)
        )

    async def _composition(
        self,
        plan: Plan | None,
        profiles: ProfileList,
        *,
        texts: Mapping[str, str] | None = None,
        info: str,
    ) -> Composition:
        """The pool of ``profiles`` with ``plan``, the default one for None; ``texts`` stand
        for stored configs."""
        sources = []
        for profile in profiles.profiles:
            if not profile.in_pool:
                continue
            text = (texts or {}).get(profile.id)
            if text is None:
                text = await asyncio.to_thread(self._store.read_config, profile.id)
            sources.append(CompositionSource(profile.id, profile.name, text))
        plan_json = plans.plan_json(plan or plans.default_plan(), self._locale())
        return Composition(tuple(sources), plan_json, info)

    # -- tunnel ------------------------------------------------------------------------------
    async def start_tunnel(self) -> None:
        """Start the routing in use with the settings' run options."""
        async with self._lock:
            options = self._settings().run_options()
            config = await self._current_config(options=options)
            if config is None:
                raise ProfileError("no_profile", "import a profile first")
            with _naming_switches(options):
                report = await self._host.start(config)
            if config.composition is not None:
                self._report = report

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
        config = await self._current_config(options=options)
        if config is None:
            raise ProfileError("no_profile", "import a profile first")
        with _naming_switches(options):
            report = await self._host.reload(config)
        if config.composition is not None:
            self._report = report

    async def _current_config(
        self, *, options: RunOptions | None = None, global_text: str | None = None
    ) -> TunnelConfig | None:
        """What the tunnel runs in the routing mode in use; None when there is nothing to
        run yet. ``global_text`` stands for the stored global override."""
        options = options or RunOptions()
        if self._state.mode == MODE_PLAN:
            composition = await self._composition(
                self._current_plan(), self._list, info=self._info_filter()
            )
            overrides = await self._global_override(global_text)
            return TunnelConfig(overrides=overrides, options=options, composition=composition)
        active = self._list.active_profile()
        if active is None:
            return None
        text = await asyncio.to_thread(self._store.read_config, active.id)
        overrides = await self._overrides(active.id, global_text=global_text)
        return TunnelConfig(text, overrides, options)

    async def _running(self) -> HostStatus | None:
        status = await self._host.get_status()
        return status if status.state == "running" else None

    async def _reload_if_running(self) -> None:
        """Reload what the routing in use runs now, when the tunnel runs."""
        status = await self._running()
        if status is None:
            return
        config = await self._current_config(options=live_options(status, self._settings()))
        if config is not None:
            report = await self._host.reload(config)
            if config.composition is not None:
                self._report = report

    async def _try_config(self, config: TunnelConfig) -> None:
        """Reload a running tunnel with ``config``, or have a stopped one's host validate it:
        either way the core accepted it before anything is stored."""
        if await self._running() is None:
            await self._host.validate(config)
        else:
            await self._reload_config_if_running(config)

    async def _reload_config_if_running(self, config: TunnelConfig) -> None:
        status = await self._running()
        if status is not None:
            options = live_options(status, self._settings())
            report = await self._host.reload(replace(config, options=options))
            if config.composition is not None:
                self._report = report

    async def _global_override(self, text: str | None = None) -> tuple[str, ...]:
        """The global override, the only one a plan's composition takes (routing.md §5.6)."""
        if text is None:
            text = await asyncio.to_thread(self._store.read_override, None)
        return (text,) if text.strip() else ()

    async def _overrides(
        self,
        profile_id: str | None,
        *,
        global_text: str | None = None,
        own_text: str | None = None,
    ) -> tuple[str, ...]:
        """The override texts that apply to a stored profile's own rules, or to a new one for
        None: the global override, then the profile's own. A text passed in stands for the
        stored one."""
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


# A profile not read yet: it may bring rules.
_UNKNOWN = SourceInspection((), True, 0, 0)
