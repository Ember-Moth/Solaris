"""The mesh networks the user added in the app (docs/mesh.md §10.2): their rows in the
database, in order.

A row holds the network without its secrets, which the system's credential store keeps
(credentials.MeshSecrets): the database goes into backups, the secrets never do. A network
lives on this device, but its row goes into backups so that the plans' ``mesh:<id>`` rules
still find it after a restore; there it waits for its secrets or a new login.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, fields, replace
from typing import Any

from solaris.host.base import (
    MESH_SECRETS,
    MESH_SETTINGS,
    EasyTierSettings,
    MeshNetwork,
    MeshSettings,
)
from solaris.models.database import Database

MESH_PREFIX = "mesh:"


def new_network_id() -> str:
    return uuid.uuid4().hex


def target(network_id: str) -> str:
    """How plans name the network: ``mesh:<id>``."""
    return MESH_PREFIX + network_id


def without_secrets(network: MeshNetwork) -> MeshNetwork:
    cleared = {f.name: "" for f in fields(network.settings) if f.name in MESH_SECRETS}
    return replace(network, settings=replace(network.settings, **cleared))


def secrets_of(network: MeshNetwork) -> dict[str, str]:
    """The network's secrets that are set, by name."""
    return {
        name: value
        for name, value in asdict(network.settings).items()
        if name in MESH_SECRETS and value
    }


def with_secrets(network: MeshNetwork, secrets: dict[str, str]) -> MeshNetwork:
    known = {f.name for f in fields(network.settings)}
    given = {
        name: value for name, value in secrets.items() if name in known and name in MESH_SECRETS
    }
    return replace(network, settings=replace(network.settings, **given))


def runnable(network: MeshNetwork) -> bool:
    """Whether the core can run the network: an EasyTier network needs its secret."""
    settings = network.settings
    return not isinstance(settings, EasyTierSettings) or bool(settings.network_secret)


def settings_json(settings: MeshSettings) -> str:
    """The settings that are no secret, as the database keeps them."""
    stored = {k: v for k, v in asdict(settings).items() if k not in MESH_SECRETS}
    return json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def settings_from(kind: str, stored: Any) -> MeshSettings | None:
    """The settings of a stored row; None for a kind this version does not know."""
    settings_type = MESH_SETTINGS.get(kind)
    if settings_type is None or not isinstance(stored, dict):
        return None
    values: dict[str, Any] = {}
    for f in fields(settings_type):
        if f.name in MESH_SECRETS or f.name not in stored:
            continue
        value = stored[f.name]
        values[f.name] = tuple(str(v) for v in value) if isinstance(value, list) else str(value)
    return settings_type(**values)


class MeshStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def networks(self) -> tuple[MeshNetwork, ...]:
        """The networks in their order, without their secrets or the deleted ones."""
        with self._database.reading() as connection:
            rows = connection.execute(
                "SELECT id, kind, name, via, settings FROM mesh_networks "
                "WHERE deleted_at IS NULL ORDER BY position, created_at"
            ).fetchall()
        networks = []
        for row in rows:
            settings = settings_from(row["kind"], json.loads(row["settings"]))
            if settings is not None:
                networks.append(MeshNetwork(row["id"], row["name"], settings, row["via"]))
        return tuple(networks)

    def put(self, network: MeshNetwork) -> None:
        """Store the network without its secrets; a new one comes last."""
        settings = settings_json(network.settings)
        with self._database.transaction() as connection:
            now = self._database.clock()
            row = connection.execute(
                "SELECT kind, name, via, settings, deleted_at FROM mesh_networks WHERE id = ?",
                (network.id,),
            ).fetchone()
            if row is None:
                (position,) = connection.execute(
                    "SELECT COALESCE(MAX(position) + 1, 0) FROM mesh_networks"
                ).fetchone()
                connection.execute(
                    "INSERT INTO mesh_networks (id, kind, name, via, settings, position, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        network.id,
                        network.kind,
                        network.name,
                        network.via,
                        settings,
                        position,
                        now,
                        now,
                    ),
                )
            elif tuple(row) != (network.kind, network.name, network.via, settings, None):
                connection.execute(
                    "UPDATE mesh_networks SET kind = ?, name = ?, via = ?, settings = ?, "
                    "updated_at = ?, deleted_at = NULL WHERE id = ?",
                    (network.kind, network.name, network.via, settings, now, network.id),
                )

    def delete(self, network_id: str) -> None:
        with self._database.transaction() as connection:
            now = self._database.clock()
            connection.execute(
                "UPDATE mesh_networks SET deleted_at = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, now, network_id),
            )
