"""The chained exits' state on this device and what the user does with it (docs/exits.md).

The WARP exit's account belongs to the device, not to a plan: it lives in the system's
credential store (credentials.py) and joins the composition of every plan that turns the
exit on (§3.2). So does the VPN Gate exit's server list (vpngate.py, §5.2), from which each
composition takes the servers its plan chooses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from solaris.host.base import CompositionReport, HostError, VpnGateServer, WarpAccount
from solaris.models import plans, vpngate
from solaris.models.credentials import CredentialError, CredentialStore, WarpAccounts
from solaris.models.profiles import ProfileError
from solaris.models.routing import MODE_PLAN, Plan

if TYPE_CHECKING:
    from solaris.models.library.core import LibraryCore, Routing

_log = logging.getLogger(__name__)


class Exits:
    def __init__(self, core: LibraryCore, credentials: CredentialStore) -> None:
        self._core = core
        self._accounts = WarpAccounts(credentials)
        self.warp: WarpAccount | None = None  # None until read, or none
        self._warp_read = False
        self.vpngate = vpngate.load(core.store.database)

    # -- the WARP exit (docs/exits.md §3.2) -----------------------------------------------------
    async def register_warp(self, *, renew: bool = False) -> Routing:
        """Register this device with WARP through the via group of the plan in use, or give
        the account it has a key for the protocol in the settings; ``renew`` registers a new
        device instead. The core registers through its running configuration, so the tunnel
        must run the plan. The account goes to the credential store, then into the tunnel."""
        core = self._core
        async with core.lock:
            via = await self._exit_via(plans.WARP, plans.warp_exit(core.current_plan()).via)
            existing = None if renew else await self._warp_account()
        # Outside the lock: Cloudflare may take a while, and plan edits need not wait for it.
        account = await core.host.register_warp(via, core.settings().warp_protocol, existing)
        async with core.lock:
            await asyncio.to_thread(self._accounts.save, account)
            self.warp, self._warp_read = account, True
            await core.recompose()
            return core.routing()

    async def forget_warp(self) -> Routing:
        """Delete this device's copy of the account; the rules that name the exit reject
        their connections from now on (D21)."""
        core = self._core
        async with core.lock:
            await asyncio.to_thread(self._accounts.delete)
            self.warp, self._warp_read = None, True
            await core.recompose()
            return core.routing()

    async def _warp_account(self) -> WarpAccount | None:
        if not self._warp_read:
            self.warp = await asyncio.to_thread(self._accounts.load)
            self._warp_read = True
        return self.warp

    async def read_warp(self) -> WarpAccount | None:
        """The account, or None while the credential store cannot be read: the exit is then
        unavailable, and its rules reject their connections."""
        try:
            return await self._warp_account()
        except CredentialError as error:
            _log.warning("cannot read the WARP account: %s", error.message)
            return None

    # -- the VPN Gate exit (docs/exits.md §5.2) -------------------------------------------------
    async def refresh_vpngate(self) -> Routing:
        """Fetch VPN Gate's list through the via group of the plan in use, and pick the exit's
        servers from it again. As with WARP, the core fetches through its running
        configuration, so the tunnel must run the plan. A running tunnel reloads only when the
        servers changed; its connections stay up."""
        core = self._core
        async with core.lock:
            via = await self._exit_via(plans.VPNGATE, plans.vpngate_exit(core.current_plan()).via)
        # Outside the lock: the list is over a megabyte, and plan edits need not wait for it.
        servers = await core.host.list_vpngate(via)
        async with core.lock:
            self.vpngate = replace(self.vpngate, servers=tuple(servers), fetched_at=core.clock())
            await self._repick_vpngate()
            return core.routing()

    async def check_vpngate(self) -> Routing:
        """Read how the exit's servers did in their last delay test: one that failed is
        skipped for an hour and its place goes to another; once fewer than half work, the
        list is fetched again (docs/exits.md §5.2), at most every ten minutes."""
        core = self._core
        async with core.lock:
            picked = self.vpngate.picked_servers()
            plan = core.current_plan()
            if (
                not picked
                or core.state.mode != MODE_PLAN
                or not plans.vpngate_exit(plan).enabled
                or await core.running() is None
            ):
                return core.routing()
            hosts = {vpngate.outbound_name(s): s.host_name for s in picked}
            delays: dict[str, int | None] = {}
            for group in await core.host.list_groups():
                for proxy in group.proxies:
                    if proxy.name in hosts:
                        delays[hosts[proxy.name]] = proxy.delay_ms
            now = core.clock()
            dead = [s.host_name for s in picked if delays.get(s.host_name) == 0]
            failed = {**self.vpngate.failed, **dict.fromkeys(dead, now)}
            self.vpngate = replace(self.vpngate, failed=failed, delays=delays)
            refresh = (len(picked) - len(dead)) * 2 < len(picked) and (
                now - self.vpngate.fetched_at >= vpngate.MIN_REFRESH_S
            )
            if dead and not refresh:
                await self._repick_vpngate()
        if refresh:
            return await self.refresh_vpngate()
        return core.routing()

    async def _exit_via(self, exit_id: str, via: str) -> str:
        """The name of an exit's via group in the running configuration, for the core to go
        out through; the tunnel must run the plan in use."""
        core = self._core
        plan = core.current_plan()
        if core.state.mode != MODE_PLAN or plan is None:
            raise ProfileError("not_routing_plan", "the tunnel does not route with a plan")
        if await core.running() is None:
            raise HostError("not_running", "the core is not running")
        report = core.report or await core.validate_plan(plan, core.list)
        if report is None:  # a composition always has one; for the type checker
            raise ProfileError("not_routing_plan", "the plan was not composed")
        core.report = report
        return _via_name(report, via, exit_id)

    async def _repick_vpngate(self) -> None:
        """Store the list, and bring the plan in use up to date when its servers changed."""
        plan = self._core.current_plan()
        before = self.vpngate.picked
        if plan is not None and plans.vpngate_exit(plan).enabled:
            chosen = self._vpngate_pick(plan)
            if tuple(s.host_name for s in chosen) != before:
                await self._core.recompose()
        await asyncio.to_thread(vpngate.save, self._core.store.database, self.vpngate)

    def _vpngate_pick(self, plan: Plan) -> tuple[VpnGateServer, ...]:
        return vpngate.pick(
            self.vpngate.servers,
            plans.vpngate_exit(plan),
            self.vpngate.picked,
            self.vpngate.failing(self._core.clock()),
        )

    async def vpngate_servers(self, plan: Plan) -> tuple[VpnGateServer, ...]:
        """The servers a composition of ``plan`` takes; for the plan in use, they become the
        exit's from now on, so the next pick keeps their positions."""
        if not plans.vpngate_exit(plan).enabled:
            return ()
        chosen = self._vpngate_pick(plan)
        current = self._core.current_plan()
        picked = tuple(s.host_name for s in chosen)
        if current is not None and current.id == plan.id and picked != self.vpngate.picked:
            self.vpngate = replace(self.vpngate, picked=picked)
            await asyncio.to_thread(vpngate.save, self._core.store.database, self.vpngate)
        return chosen


def _via_name(report: CompositionReport, via: str, exit_id: str) -> str:
    """The via group's name in the running configuration; 节点选择's when it has no members,
    as the exit itself goes through 节点选择 then (docs/exits.md §3.3)."""
    names = {group.id: group.name for group in report.groups}
    empty = any(w.entry_id == exit_id and w.reason == "via_empty" for w in report.warnings)
    return names.get(plans.PROXY, "") if empty or via not in names else names[via]
