"""The mesh networks the user added in the app (docs/mesh.md §10).

Every composition carries all of them, with their secrets from the system's credential
store; the database keeps them without.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from typing import TYPE_CHECKING

from solaris.host.base import MeshNetwork, MeshStatus, TunnelConfig
from solaris.models import mesh
from solaris.models.credentials import CredentialError, CredentialStore, MeshSecrets

if TYPE_CHECKING:
    from solaris.models.library.core import LibraryCore, Routing

_log = logging.getLogger(__name__)


class Networks:
    def __init__(self, core: LibraryCore, credentials: CredentialStore) -> None:
        self._core = core
        self._store = mesh.MeshStore(core.store.database)
        self._secrets_store = MeshSecrets(credentials)
        self.networks = self._store.networks()  # without their secrets
        # Each network's secrets by id; None until the credential store has been read.
        self._secrets: dict[str, dict[str, str]] | None = None

    def secret_names(self) -> dict[str, frozenset[str]]:
        """The names of the secrets the credential store holds for each network, by id;
        empty until read."""
        return {
            network_id: frozenset(secrets) for network_id, secrets in (self._secrets or {}).items()
        }

    async def put_network(
        self, network: MeshNetwork, keep_secrets: Collection[str] = ()
    ) -> Routing:
        """Add or change a mesh network once the host accepts the composition with it, and
        reload a tunnel that routes with the plan in use. ``network`` brings its secrets;
        those named in ``keep_secrets`` that it leaves empty keep the stored ones."""
        core = self._core
        async with core.lock:
            stored = await self._network_secrets()
            kept = {
                name: value
                for name, value in stored.get(network.id, {}).items()
                if name in keep_secrets
            }
            network = mesh.with_secrets(network, {**kept, **mesh.secrets_of(network)})
            public = mesh.without_secrets(network)
            networks = tuple(n for n in self.networks if n.id != network.id)
            if len(networks) == len(self.networks):
                networks = (*networks, public)
            else:
                networks = tuple(public if n.id == network.id else n for n in self.networks)
            with_secrets = tuple(
                network if n.id == network.id else mesh.with_secrets(n, stored.get(n.id, {}))
                for n in networks
            )
            plan = core.current_plan()
            composition = await core.composition(
                plan, core.list, info=core.info_filter(), networks=with_secrets
            )
            report = await core.host.validate(
                TunnelConfig(overrides=await core.global_override(), composition=composition)
            )
            secrets = mesh.secrets_of(network)
            await asyncio.to_thread(self._secrets_store.save, network.id, secrets)
            await asyncio.to_thread(self._store.put, public)
            stored[network.id] = secrets
            self.networks = await asyncio.to_thread(self._store.networks)
            await core.recompose(report)
            return core.routing()

    async def remove_network(self, network_id: str) -> Routing:
        """Remove a mesh network with its secrets; its rules reject their connections from
        now on, and the core removes its state at the next start or reload. The network
        leaves the tunnel even while the credential store cannot be written: its secret
        stays behind there, and the log says so."""
        core = self._core
        async with core.lock:
            await asyncio.to_thread(self._store.delete, network_id)
            self.networks = await asyncio.to_thread(self._store.networks)
            if self._secrets is not None:
                self._secrets.pop(network_id, None)
            try:
                await asyncio.to_thread(self._secrets_store.delete, network_id)
            except CredentialError as error:
                _log.warning("cannot delete a mesh network's secrets: %s", error.message)
            await core.recompose()
            return core.routing()

    async def mesh_status(self) -> list[MeshStatus]:
        """How the running configuration's mesh outbounds stand; HostError("not_running")
        while the tunnel is stopped."""
        return await self._core.host.get_mesh_status()

    async def login_mesh(self, name: str) -> str:
        """The URL that logs the running mesh outbound ``name`` in, to open in the browser."""
        return await self._core.host.login_mesh(name)

    async def _network_secrets(self) -> dict[str, dict[str, str]]:
        """Every network's secrets, read from the credential store once."""
        if self._secrets is None:
            secrets = {}
            for network in self.networks:
                secrets[network.id] = await asyncio.to_thread(self._secrets_store.load, network.id)
            self._secrets = secrets
        return self._secrets

    async def read_secrets(self) -> dict[str, dict[str, str]]:
        """The secrets, or none while the credential store cannot be read: Tailscale and
        ZeroTier then run on the login and identity the core keeps, and an EasyTier network
        waits for its secret."""
        try:
            return await self._network_secrets()
        except CredentialError as error:
            _log.warning("cannot read the mesh networks' secrets: %s", error.message)
            return {}

    async def for_composition(
        self, networks: tuple[MeshNetwork, ...] | None
    ) -> tuple[MeshNetwork, ...]:
        """The networks a composition carries: with their secrets, and without those the
        core cannot run yet. ``networks``, with their secrets, stand for the stored ones."""
        if networks is None:
            secrets = await self.read_secrets()
            networks = tuple(mesh.with_secrets(n, secrets.get(n.id, {})) for n in self.networks)
        return tuple(n for n in networks if mesh.runnable(n))

    async def take_restored(self) -> None:
        """Read the networks a restore wrote; the secrets of those it removed go."""
        before = {network.id for network in self.networks}
        self.networks = await asyncio.to_thread(self._store.networks)
        for network_id in before - {network.id for network in self.networks}:
            try:
                await asyncio.to_thread(self._secrets_store.delete, network_id)
            except CredentialError as error:
                _log.warning("cannot delete a mesh network's secrets: %s", error.message)
        self._secrets = None
