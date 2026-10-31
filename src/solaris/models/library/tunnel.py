"""Starting the tunnel with the routing in use, and the settings a running tunnel takes up
without stopping.

While no core can answer (macOS with the tunnel down, D27), profiles are stored unread and
changes to the routing unchecked. At the next connect the tunnel comes up first, the core
reads the unread profiles, the import settles what it would have, and Start validates the
rest.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

from solaris.host.base import HostError, RunOptions, SourceInspection
from solaris.models import plans
from solaris.models.library.core import has_rules, live_options
from solaris.models.profiles import ProfileError
from solaris.models.routing import MODE_PLAN, MODE_PROFILE
from solaris.models.settings import Settings

if TYPE_CHECKING:
    from solaris.models.library.core import LibraryCore


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


class Tunnel:
    def __init__(self, core: LibraryCore) -> None:
        self._core = core

    async def start(self) -> None:
        """Start the routing in use with the settings' run options. Profiles stored while no
        core could read them are read first, with the tunnel up for Start (D27); when that
        or Start fails, the tunnel goes down again."""
        core = self._core
        async with core.lock:
            options = core.settings().run_options()
            prepared = any(p.unread for p in core.list.profiles)
            if prepared:
                await core.host.prepare_start(options)
            try:
                if prepared:
                    await self._read_unread()
                config = await core.current_config(options=options)
                if config is None:
                    raise ProfileError("no_profile", "import a profile first")
                with _naming_switches(options):
                    report = await core.host.start(config)
            except BaseException:
                if prepared:
                    with contextlib.suppress(HostError):
                        await core.host.stop()
                raise
            if config.composition is not None:
                core.report = report

    async def _read_stored(self, profile_id: str) -> SourceInspection:
        core = self._core
        text = await asyncio.to_thread(core.store.read_config, profile_id)
        inspection = await core.read(text, profile_id)
        if inspection is None:
            raise HostError("not_running", "no core answers")
        return inspection

    async def _read_unread(self) -> None:
        """Have the core read the profiles stored while none could, and settle what their
        import would have: the routing mode, the profile whose rules are used. When the core
        refuses an update, the text it read before stays, as after any failed update; a
        profile without one stays unread. Either shows as a failed update. Raises when the
        profile Start would run stays unread."""
        core = self._core
        refused: dict[str, Exception] = {}
        mode = core.state.mode
        updated = core.list
        for profile in [p for p in core.list.profiles if p.unread]:
            try:
                inspection = await self._read_stored(profile.id)
            except HostError as error:
                if error.code != "invalid_config":
                    raise
                core.mark_failed(profile.id, core.clock())
                if not await asyncio.to_thread(core.store.fall_back, profile.id):
                    refused[profile.id] = error
                    continue
                inspection = await self._read_stored(profile.id)
            else:
                core.mark_failed(profile.id, None)
            read = replace(
                profile,
                proxy_count=inspection.node_count,
                has_rules=inspection.has_rules,
                unread=False,
            )
            updated = updated.put(read)
            core.inspections[profile.id] = inspection
            if updated.active is None and inspection.has_rules:
                updated = replace(updated, active=profile.id)
            if mode is None:  # the first import picks the routing (routing.md §5.1)
                mode = MODE_PROFILE if inspection.has_rules else MODE_PLAN
        active = updated.active_profile()
        if mode == MODE_PROFILE and (
            active is None or has_rules(active, core.inspections) is False
        ):
            # Picked while unread, and it has nodes only: the earliest read one with rules.
            read = replace(updated, profiles=tuple(p for p in updated.profiles if not p.unread))
            successor = core.first_with_rules(read)
            updated = replace(updated, active=successor.id if successor else None)
            mode = MODE_PROFILE if successor else MODE_PLAN
        await core.save(updated)
        core.report = None  # the pool may have more nodes
        if mode is not None and mode != core.state.mode:
            plan = (core.current_plan() or plans.default_plan()) if mode == MODE_PLAN else None
            await core.store_mode(mode, plan)
        in_use = core.list.active if core.state.mode == MODE_PROFILE else None
        if in_use in refused:
            raise refused[in_use]
        if core.state.mode is None and refused:
            raise next(iter(refused.values()))

    async def set_tun(self, enabled: bool) -> None:
        """Reload the running tunnel with TUN on or off; a stopped one starts with the
        setting, so there is nothing to do."""
        core = self._core
        async with core.lock:
            status = await core.running()
            if status is not None:
                options = replace(live_options(status, core.settings()), tun_enabled=enabled)
                await self._reload_active(options)

    async def set_system_proxy(self, enabled: bool) -> None:
        core = self._core
        async with core.lock:
            if await core.running() is None:
                return
            with _naming_switches(RunOptions(system_proxy_enabled=enabled)):
                await core.host.set_system_proxy(enabled)

    async def apply_settings(self, settings: Settings) -> None:
        """Reload the running tunnel with the mixed port and LAN access of ``settings``,
        before they are stored: if the core refuses them, the old ones stay in use."""
        core = self._core
        async with core.lock:
            status = await core.running()
            if status is not None:
                await self._reload_active(live_options(status, settings))

    async def _reload_active(self, options: RunOptions) -> None:
        core = self._core
        config = await core.current_config(options=options)
        if config is None:
            raise ProfileError("no_profile", "import a profile first")
        with _naming_switches(options):
            report = await core.host.reload(config)
        if config.composition is not None:
            core.report = report
