"""The user's profiles and routing, kept in step with the running tunnel (docs/routing.md §5).

``ProfileLibrary`` is what the view models hold. Its parts share one core (core.py: the
stores, the host, the one lock, and the steps every change goes through) and each owns one
responsibility:

    profiles   import, update, use and remove profiles, their overrides, restoring a backup
    plans      the routing mode, the plans, the pool settings, the routing editor's queries
    exits      the WARP account and the VPN Gate list (docs/exits.md)
    networks   the mesh networks added in the app (docs/mesh.md §10)
    tunnel     starting the tunnel (D27) and the settings a running tunnel takes up

core.py describes the lock.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from solaris.host.base import TunnelHost
from solaris.models import plans as plan_model
from solaris.models.credentials import CredentialStore
from solaris.models.library.core import LibraryCore, Routing, live_options
from solaris.models.library.exits import Exits
from solaris.models.library.networks import Networks
from solaris.models.library.plans import Plans, ProfileRules
from solaris.models.library.profiles import (
    DEFAULT_AUTO_UPDATE_HOURS,
    RETRY_AFTER_S,
    Profiles,
    Removal,
)
from solaris.models.library.tunnel import Tunnel
from solaris.models.profiles import ProfileList
from solaris.models.settings import Settings
from solaris.models.store import ProfileStore
from solaris.models.subscription import Fetcher

__all__ = [
    "DEFAULT_AUTO_UPDATE_HOURS",
    "RETRY_AFTER_S",
    "ProfileLibrary",
    "ProfileRules",
    "Removal",
    "Routing",
    "live_options",
]


class ProfileLibrary:
    def __init__(
        self,
        store: ProfileStore,
        host: TunnelHost,
        fetcher: Fetcher,
        clock: Callable[[], float] = time.time,
        settings: Callable[[], Settings] = Settings,
        locale: Callable[[], str] = plan_model.ui_locale,
        credentials: CredentialStore | None = None,
    ) -> None:
        """``settings`` returns the current settings; it is read on the asyncio thread.
        ``locale`` names the groups the core generates for a plan (routing.md §8.5).
        ``credentials`` keeps the WARP account and the mesh networks' secrets; the system's
        store by default."""
        self._core = LibraryCore(store, host, clock, settings, locale, credentials)
        self.profiles = Profiles(self._core, fetcher)
        self.plans = Plans(self._core)
        self.exits: Exits = self._core.exits
        self.networks: Networks = self._core.networks
        self.tunnel = Tunnel(self._core)

    def now(self) -> float:
        """The library's clock, which dates what it stores."""
        return self._core.clock()

    def snapshot(self) -> ProfileList:
        """The profiles as loaded; call before the asyncio thread starts using the library."""
        return self._core.list

    def routing_snapshot(self) -> Routing:
        """The routing as loaded; call before the asyncio thread starts using the library."""
        return self._core.routing()

    async def current(self) -> ProfileList:
        return self._core.list

    async def routing(self) -> Routing:
        return self._core.routing()

    @property
    def core_available(self) -> bool:
        """A core can answer now (TunnelHost.core_available): on macOS, only while the
        tunnel is connected."""
        return self._core.host.core_available

    async def inspect_all(self) -> Routing:
        """Read the profiles not read yet, so the routing page knows which bring rules, the
        WARP account and the mesh networks' secrets. A profile the host cannot read stays
        unknown."""
        await self._core.inspect_missing()
        await self._core.exits.read_warp()
        await self._core.networks.read_secrets()
        return self._core.routing()
