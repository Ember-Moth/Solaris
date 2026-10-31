"""The routing the user chose: the mode, the plans, the node pool's settings, and what the
routing page and its editors ask of the core (docs/routing.md §4, §5).

Every change to the routing is validated, then stored, then reloaded into a running tunnel
(§5.4).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from solaris.host.base import (
    GeoCategories,
    GroupPreview,
    HostError,
    PlanConversion,
    ProxyGroup,
    Rule,
    RuleMatch,
    TunnelConfig,
    bundled_geo_categories,
)
from solaris.models import plans
from solaris.models.library.core import has_rules, live_options
from solaris.models.profiles import ProfileError
from solaris.models.routing import (
    DEFAULT_INFO_PATTERN,
    MODE_PLAN,
    MODE_PROFILE,
    MODES,
    Plan,
    PlanGroup,
)
from solaris.models.subscription import read_config_file

if TYPE_CHECKING:
    from solaris.models.library.core import LibraryCore, Routing


@dataclass(frozen=True)
class ProfileRules:
    """The groups and rules of the profile in use, as the running core reports them."""

    groups: tuple[ProxyGroup, ...]
    rules: tuple[Rule, ...]


class Plans:
    def __init__(self, core: LibraryCore) -> None:
        self._core = core

    # -- the mode, the plans, the pool settings ------------------------------------------------
    async def set_mode(self, mode: str) -> Routing:
        """Route with the active profile's rules or with the plan in use. The first switch to
        plans makes the default plan; switching to a profile's rules needs a profile with
        some, the active one or else the earliest."""
        if mode not in MODES:
            raise ValueError(f"invalid mode: {mode!r}")
        core = self._core
        async with core.lock:
            if mode == core.state.mode:
                return core.routing()
            if mode == MODE_PROFILE:
                await core.inspect_missing()
                active = core.list.active_profile()
                if active is None or has_rules(active, core.inspections) is False:
                    active = core.first_with_rules(core.list)
                if active is None:
                    raise ProfileError("no_rules", "no profile has rules of its own")
                text = await asyncio.to_thread(core.store.read_config, active.id)
                config = TunnelConfig(text, await core.overrides(active.id))
                await core.try_config(config)
                if active.id != core.list.active:
                    await core.save(replace(core.list, active=active.id))
                await core.store_mode(MODE_PROFILE, None)
            else:
                plan = core.current_plan() or plans.default_plan()
                await core.apply_plan(plan, current=True, routing=True)
                await core.store_mode(MODE_PLAN, plan)
            return core.routing()

    async def select_plan(self, plan_id: str) -> Routing:
        core = self._core
        async with core.lock:
            plan = next(p for p in core.plans if p.id == plan_id)
            if plan.id != core.state.plan_id:
                await core.apply_plan(plan, current=True)
                core.state = await asyncio.to_thread(
                    core.routing_store.update_state, plan_id=plan.id
                )
            return core.routing()

    async def put_plan(self, plan: Plan) -> Routing:
        """Store a plan once the host accepts it, and reload a running tunnel that routes
        with it. A new plan comes last; putting an earlier version back undoes a change."""
        core = self._core
        async with core.lock:
            await core.apply_plan(plan)
            return core.routing()

    async def create_plan(self, name: str, base: str | None) -> Routing:
        """A new plan from the template, or a copy of the plan ``base``; it comes into use."""
        core = self._core
        async with core.lock:
            if base is None:
                plan = plans.default_plan(name=name)
            else:
                plan = plans.copy_plan(next(p for p in core.plans if p.id == base), name)
            await core.apply_plan(plan, current=True)
            core.state = await asyncio.to_thread(core.routing_store.update_state, plan_id=plan.id)
            return core.routing()

    async def delete_plan(self, plan_id: str) -> Routing:
        """Delete a plan; the last one stays. The next plan takes over the one in use."""
        core = self._core
        async with core.lock:
            if len(core.plans) <= 1:
                raise ProfileError("last_plan", "the last plan cannot be deleted")
            current = core.current_plan()
            if current is not None and current.id == plan_id:
                successor = next(p for p in core.plans if p.id != plan_id)
                await core.apply_plan(successor, current=True)
                core.state = await asyncio.to_thread(
                    core.routing_store.update_state, plan_id=successor.id
                )
            await asyncio.to_thread(core.routing_store.delete_plan, plan_id)
            core.plans = await asyncio.to_thread(core.routing_store.plans)
            return core.routing()

    async def set_info_pattern(self, pattern: str | None) -> Routing:
        """The expression that keeps information nodes out of the pool; None restores the
        built-in one. A pattern the core cannot use is refused."""
        core = self._core
        async with core.lock:
            info = DEFAULT_INFO_PATTERN if pattern is None else pattern
            plan = core.current_plan()
            composition = await core.composition(plan, core.list, info=info)
            report = await core.check(
                TunnelConfig(overrides=await core.global_override(), composition=composition)
            )
            core.state = await asyncio.to_thread(
                core.routing_store.update_state, info_pattern=pattern
            )
            core.inspections.clear()  # which nodes are information nodes changed
            await core.inspect_missing()
            core.report = report if plan is not None else None
            if core.state.mode == MODE_PLAN:
                await core.reload_if_running()
            return core.routing()

    async def add_plan(self, plan: Plan, use: bool) -> Routing:
        """Store a plan made elsewhere, such as a conversion; ``use`` routes with it from now."""
        core = self._core
        async with core.lock:
            await core.apply_plan(plan, current=use, routing=use)
            if use:
                await core.store_mode(MODE_PLAN, plan)
            return core.routing()

    # -- what the routing page shows -----------------------------------------------------------
    async def compose_report(self) -> Routing:
        """Compose the plan in use with the current pool when that has not happened yet, for
        the routing page: the regions with nodes, the warnings of its rules."""
        core = self._core
        async with core.lock:
            plan = core.current_plan()
            if core.report is None and plan is not None:
                core.report = await core.validate_plan(plan, core.list)
            return core.routing()

    async def profile_rules(self, profile_id: str) -> ProfileRules | None:
        """The groups and rules the core runs when it runs this profile's own rules; None
        when it runs something else, or nothing."""
        core = self._core
        if core.state.mode != MODE_PROFILE or profile_id != core.list.active:
            return None
        if await core.running() is None:
            return None
        groups = await core.host.list_groups()
        rules = await core.host.list_rules()
        return ProfileRules(tuple(groups), tuple(rules))

    async def geo_categories(self) -> GeoCategories:
        """The categories the add-rule dialog lists: the core's, or the installer's list
        while the core cannot be asked."""
        try:
            return await self._core.host.list_geo_categories()
        except HostError:
            return bundled_geo_categories()

    async def node_delays(self) -> dict[str, int | None]:
        """The delays the running core measured with the test the settings choose, by node
        name; empty while it is stopped."""
        core = self._core
        if await core.running() is None:
            return {}
        groups = await core.host.list_groups()
        test = core.settings().delay_test
        return {proxy.name: proxy.delay(test) for group in groups for proxy in group.proxies}

    # -- the routing editor: previews, the rule test, conversion and export ------------------
    async def preview_group(self, plan: Plan, group: PlanGroup) -> GroupPreview:
        """The members ``group`` gets in ``plan`` with the pool, as the group editor previews
        them (routing.md §4.5)."""
        core = self._core
        groups = list(plan.groups)
        index = next((i for i, g in enumerate(groups) if g.id == group.id), None)
        if index is None:
            groups.append(group)
        else:
            groups[index] = group
        draft = replace(plan, groups=tuple(groups))
        composition = await core.composition(draft, core.list, info=core.info_filter())
        return await core.host.preview_group(composition, group.id)

    async def match_rule(self, plan: Plan, host: str, port: int = 0) -> RuleMatch:
        """The rule of ``plan`` a connection to ``host`` matches, with the pool and the global
        override as the tunnel would run it (routing.md §8.3)."""
        core = self._core
        composition = await core.composition(plan, core.list, info=core.info_filter())
        config = TunnelConfig(overrides=await core.global_override(), composition=composition)
        return await core.host.match_rule(host, port, config=config)

    async def convert_profile(self, profile_id: str) -> PlanConversion:
        """The profile's groups and rules as a plan whose groups pick its nodes (§4.7)."""
        core = self._core
        core.list.get(profile_id)
        text = await asyncio.to_thread(core.store.read_config, profile_id)
        return await core.host.convert_to_plan(
            text, source_id=profile_id, locale=core.locale(), info_filter=core.info_filter()
        )

    async def convert_file(self, path: Path) -> PlanConversion:
        """A mihomo configuration's groups and rules as a plan; its nodes count by name."""
        core = self._core
        text = await asyncio.to_thread(read_config_file, path)
        return await core.host.convert_to_plan(
            text, locale=core.locale(), info_filter=core.info_filter()
        )

    async def export_plan(self, plan: Plan, export: str) -> str:
        """``plan`` as YAML to share (EXPORT_SHARE), or the whole configuration the core would
        run with it, the global override and the run options (EXPORT_GENERATED)."""
        core = self._core
        composition = await core.composition(plan, core.list, info=core.info_filter())
        status = await core.running()
        settings = core.settings()
        options = live_options(status, settings) if status else settings.run_options()
        config = TunnelConfig(
            overrides=await core.global_override(), options=options, composition=composition
        )
        return await core.host.export_plan(config, export)
