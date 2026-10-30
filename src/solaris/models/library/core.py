"""What the parts of the library share: the stores, the host, the one lock, the profiles and
the routing state, and the steps every change goes through. A change is read by the core,
validated, stored, then reloaded into a running tunnel (docs/routing.md §5.4); the core
here does the reading, composing and reloading, and the parts decide what to change.

The routing mode decides what the tunnel runs:

    订阅自带 (MODE_PROFILE)  the active profile's own YAML, with the global override and the
                             profile's own (routing.md §5.6)
    我的方案 (MODE_PLAN)     a composition: the profiles in the node pool, in list order, and
                             the plan in use; only the global override applies

A composition also carries what the exits and the mesh networks add to every plan (their
parts own that state): the WARP account and the VPN Gate servers a plan chooses
(docs/exits.md §3.2, §5.2), and the networks the user added, with their secrets
(docs/mesh.md §10).

Locking: there is one ``asyncio.Lock``, ``LibraryCore.lock``. A part's public method that
changes state takes it around the whole change; the core's methods and the parts' private
ones run under it and never take it, as an ``asyncio.Lock`` cannot be taken twice.
Downloads, registrations and other requests that take a while run outside it, so an
automatic update cannot interleave with a switch or a plan edit, and a plan edit need not
wait for a download. Everything runs on the asyncio thread.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from solaris.host.base import (
    Composition,
    CompositionReport,
    CompositionSource,
    Egress,
    HostError,
    HostStatus,
    MeshNetwork,
    RunOptions,
    SourceInspection,
    TunnelConfig,
    TunnelHost,
    WarpAccount,
)
from solaris.models import plans
from solaris.models.credentials import CredentialStore, KeyringCredentials
from solaris.models.library.exits import Exits
from solaris.models.library.networks import Networks
from solaris.models.profiles import Profile, ProfileError, ProfileList
from solaris.models.routing import (
    DEFAULT_INFO_PATTERN,
    MODE_PLAN,
    Plan,
    RoutingState,
    RoutingStore,
)
from solaris.models.settings import Settings
from solaris.models.store import ProfileStore
from solaris.models.vpngate import ServerList


@dataclass(frozen=True)
class Routing:
    """The routing as the routing page shows it."""

    state: RoutingState  # profile_id is ProfileList.active
    plans: tuple[Plan, ...]
    # What the core made of the plan in use at its last validate, start or reload; None
    # until it has been composed with the current pool.
    report: CompositionReport | None
    inspections: Mapping[str, SourceInspection]  # by profile id; missing until read
    warp: WarpAccount | None = None  # this device's WARP account; None until read, or none
    vpngate: ServerList = field(default_factory=ServerList)  # this device's VPN Gate list
    networks: tuple[MeshNetwork, ...] = ()  # the mesh networks, without their secrets
    # The names of the secrets the credential store holds for each network, by id; empty
    # until read.
    secrets: Mapping[str, frozenset[str]] = field(default_factory=dict)

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

    def has_rules(self, profile: Profile) -> bool | None:
        """Whether the profile brings rules; None until a core read it."""
        return has_rules(profile, self.inspections)


def live_options(status: HostStatus, settings: Settings) -> RunOptions:
    """Options that keep the TUN and system proxy as the tunnel runs them."""
    return replace(
        settings.run_options(),
        tun_enabled=status.tun_enabled,
        system_proxy_enabled=status.system_proxy_enabled,
    )


def has_rules(profile: Profile, inspections: Mapping[str, SourceInspection]) -> bool | None:
    """Whether the profile brings rules, as the core read it this run or when it was stored;
    None until a core read it."""
    inspection = inspections.get(profile.id)
    return profile.has_rules if inspection is None else inspection.has_rules


class LibraryCore:
    def __init__(
        self,
        store: ProfileStore,
        host: TunnelHost,
        clock: Callable[[], float] = time.time,
        settings: Callable[[], Settings] = Settings,
        locale: Callable[[], str] = plans.ui_locale,
        credentials: CredentialStore | None = None,
    ) -> None:
        """``settings`` returns the current settings; it is read on the asyncio thread.
        ``locale`` names the groups the core generates for a plan (routing.md §8.5).
        ``credentials`` keeps the WARP account and the mesh networks' secrets; the system's
        store by default."""
        self.store = store
        self.routing_store = RoutingStore(store.database)
        self.host = host
        self.clock = clock
        self.settings = settings
        self.locale = locale
        self.lock = asyncio.Lock()
        self.list = store.load()
        self.failed: dict[str, float] = {}  # profile id -> time of the failed update
        self.inspections: dict[str, SourceInspection] = {}
        self.report: CompositionReport | None = None
        # None until the first import picks the mode (routing.md §5.1).
        self.state = self.routing_store.state()
        self.plans = self.routing_store.plans()
        credentials = credentials or KeyringCredentials()
        self.exits = Exits(self, credentials)
        self.networks = Networks(self, credentials)

    def routing(self) -> Routing:
        return Routing(
            replace(self.state, profile_id=self.list.active),
            self.plans,
            self.report,
            dict(self.inspections),
            self.exits.warp,
            self.exits.vpngate,
            self.networks.networks,
            self.networks.secret_names(),
        )

    def current_plan(self) -> Plan | None:
        return self.routing().plan()

    # -- reading profiles as the core does ---------------------------------------------------
    def info_filter(self) -> str:
        pattern = self.state.info_pattern
        return DEFAULT_INFO_PATTERN if pattern is None else pattern

    async def inspect(self, text: str) -> SourceInspection:
        return await self.host.inspect_source(text, self.info_filter())

    async def inspect_missing(self) -> None:
        """Read the profiles not read yet, so the routing page knows which bring rules. A
        profile the host cannot read stays unknown."""
        if not self.host.core_available:
            return
        for profile in self.list.profiles:
            if profile.id in self.inspections or profile.unread:
                continue
            try:
                text = await asyncio.to_thread(self.store.read_config, profile.id)
                self.inspections[profile.id] = await self.inspect(text)
            except HostError, ProfileError:
                continue

    async def read(self, text: str, profile_id: str | None) -> SourceInspection | None:
        """Have the core read a profile's text: whether it brings rules, and with rules,
        that they run as a configuration of their own with the overrides of ``profile_id``
        (None for a new profile). None while no core can answer."""
        if not self.host.core_available:
            return None
        inspection = await self.inspect(text)
        if inspection.has_rules:
            await self.host.validate(TunnelConfig(text, await self.overrides(profile_id)))
        return inspection

    async def check(self, config: TunnelConfig) -> CompositionReport | None:
        """Have the core validate ``config``; its report for a composition. While no core can
        answer, nothing is checked: Start validates the configuration at the next connect."""
        if not self.host.core_available:
            return None
        return await self.host.validate(config)

    def first_with_rules(self, profiles: ProfileList, exclude: str = "") -> Profile | None:
        """The earliest imported profile with rules of its own (routing.md §5.1)."""
        return next(
            (
                p
                for p in profiles.profiles
                if p.id != exclude and has_rules(p, self.inspections) is not False
            ),
            None,
        )

    # -- storing -----------------------------------------------------------------------------
    async def save(self, profiles: ProfileList) -> None:
        await asyncio.to_thread(self.store.save, profiles)
        # Failures may have been marked while the index was written.
        self.list = replace(profiles, update_failed=frozenset(self.failed))

    def mark_failed(self, profile_id: str, failed_at: float | None) -> None:
        if failed_at is None:
            self.failed.pop(profile_id, None)
        else:
            self.failed[profile_id] = failed_at
        self.list = replace(self.list, update_failed=frozenset(self.failed))

    async def store_mode(self, mode: str, plan: Plan | None) -> None:
        if plan is not None and plan.id not in {p.id for p in self.plans}:
            await asyncio.to_thread(self.routing_store.put_plan, plan)
            self.plans = await asyncio.to_thread(self.routing_store.plans)
        changes: dict[str, str] = {"mode": mode}
        if plan is not None:
            changes["plan_id"] = plan.id
        self.state = await asyncio.to_thread(self.routing_store.update_state, **changes)

    # -- overrides: ``None`` is the global override, a profile id that profile's own ----------
    async def global_override(self, text: str | None = None) -> tuple[str, ...]:
        """The global override, the only one a plan's composition takes (routing.md §5.6)."""
        if text is None:
            text = await asyncio.to_thread(self.store.read_override, None)
        return (text,) if text.strip() else ()

    async def overrides(
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
            global_text = await asyncio.to_thread(self.store.read_override, None)
        if own_text is None:
            own_text = (
                await asyncio.to_thread(self.store.read_override, profile_id)
                if profile_id is not None
                else ""
            )
        return tuple(text for text in (global_text, own_text) if text.strip())

    # -- composing a plan --------------------------------------------------------------------
    async def composition(
        self,
        plan: Plan | None,
        profiles: ProfileList,
        *,
        texts: Mapping[str, str] | None = None,
        info: str,
        networks: tuple[MeshNetwork, ...] | None = None,
    ) -> Composition:
        """The pool of ``profiles`` with ``plan``, the default one for None; ``texts`` stand
        for stored configs, and ``networks``, with their secrets, for the stored ones."""
        sources = []
        for profile in profiles.profiles:
            if not profile.in_pool or (profile.unread and profile.id not in (texts or {})):
                continue  # an unread profile brings its nodes once the core read it
            text = (texts or {}).get(profile.id)
            if text is None:
                try:
                    text = await asyncio.to_thread(self.store.read_config, profile.id)
                except ProfileError as error:
                    # Restored, and not downloaded yet (docs/storage.md §3): no nodes.
                    if error.code != "config_missing":
                        raise
                    continue
            sources.append(CompositionSource(profile.id, profile.name, text))
        plan = plan or plans.default_plan()
        plan_json = plans.plan_json(plan, self.locale())
        # A plan that turns an exit off gets no account or servers: its rules reject (D21).
        warp = await self.exits.read_warp() if plans.warp_exit(plan).enabled else None
        servers = await self.exits.vpngate_servers(plan)
        return Composition(
            tuple(sources),
            plan_json,
            info,
            Egress(warp, servers),
            await self.networks.for_composition(networks),
        )

    async def validate_plan(
        self,
        plan: Plan | None,
        profiles: ProfileList,
        *,
        texts: Mapping[str, str] | None = None,
    ) -> CompositionReport | None:
        composition = await self.composition(plan, profiles, texts=texts, info=self.info_filter())
        return await self.check(
            TunnelConfig(overrides=await self.global_override(), composition=composition)
        )

    async def apply_plan(
        self, plan: Plan, *, current: bool | None = None, routing: bool | None = None
    ) -> None:
        """Validate a plan, store it, and reload a tunnel that routes with it. ``current``:
        the plan is, or becomes, the plan in use; ``routing``: the tunnel routes with it, or
        is about to. By default both follow the routing state."""
        if current is None:
            chosen = self.current_plan()
            current = chosen is None or chosen.id == plan.id
        if routing is None:
            routing = current and self.state.mode == MODE_PLAN
        composition = await self.composition(plan, self.list, info=self.info_filter())
        config = TunnelConfig(overrides=await self.global_override(), composition=composition)
        report = await self.check(config)
        await asyncio.to_thread(self.routing_store.put_plan, plan)
        self.plans = await asyncio.to_thread(self.routing_store.plans)
        if current:
            self.report = report
        if routing:
            await self.reload_config_if_running(config)

    async def recompose(self, report: CompositionReport | None = None) -> None:
        """Bring the report and a tunnel that routes with the plan in use up to date with
        something outside the plan, such as the WARP account; ``report`` is what Validate
        made of it already."""
        plan = self.current_plan()
        if plan is None:
            return
        if self.state.mode == MODE_PLAN and await self.running() is not None:
            await self.reload_if_running()
        else:
            self.report = report or await self.validate_plan(plan, self.list)

    # -- the tunnel --------------------------------------------------------------------------
    async def current_config(
        self, *, options: RunOptions | None = None, global_text: str | None = None
    ) -> TunnelConfig | None:
        """What the tunnel runs in the routing mode in use; None when there is nothing to
        run yet. ``global_text`` stands for the stored global override."""
        options = options or RunOptions()
        if self.state.mode == MODE_PLAN:
            composition = await self.composition(
                self.current_plan(), self.list, info=self.info_filter()
            )
            overrides = await self.global_override(global_text)
            return TunnelConfig(overrides=overrides, options=options, composition=composition)
        active = self.list.active_profile()
        if active is None:
            return None
        text = await asyncio.to_thread(self.store.read_config, active.id)
        overrides = await self.overrides(active.id, global_text=global_text)
        return TunnelConfig(text, overrides, options)

    async def running(self) -> HostStatus | None:
        status = await self.host.get_status()
        return status if status.state == "running" else None

    async def reload_if_running(self) -> None:
        """Reload what the routing in use runs now, when the tunnel runs."""
        status = await self.running()
        if status is None:
            return
        config = await self.current_config(options=live_options(status, self.settings()))
        if config is not None:
            report = await self.host.reload(config)
            if config.composition is not None:
                self.report = report

    async def try_config(self, config: TunnelConfig) -> None:
        """Reload a running tunnel with ``config``, or have a stopped one's host validate it:
        either way the core accepted it before anything is stored."""
        if await self.running() is None:
            await self.check(config)
        else:
            await self.reload_config_if_running(config)

    async def reload_config_if_running(self, config: TunnelConfig) -> None:
        status = await self.running()
        if status is not None:
            options = live_options(status, self.settings())
            report = await self.host.reload(replace(config, options=options))
            if config.composition is not None:
                self.report = report
