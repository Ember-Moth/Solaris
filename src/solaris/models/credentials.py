"""Credentials in the system's credential store, not in the database or a backup (decision
D14): the macOS keychain, the Windows Credential Manager. The first one kept there is the
WARP exit's account (docs/exits.md §3.2); a new device registers one of its own. The mesh
networks' secrets follow (docs/mesh.md §10.2).

Code depends on ``CredentialStore``; ``KeyringCredentials`` is the system's store through
the keyring package. The tests swap ``system_keyring`` for one in memory, so they never
touch the real store.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, fields
from typing import Protocol

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from solaris.host.base import MESH_SECRETS, WARP_PROTOCOLS, WarpAccount
from solaris.models.profiles import ProfileError

SERVICE = "Solaris"
WARP_ACCOUNT = "warp-account"
MESH_SECRET = "mesh:{id}"  # a mesh network's secrets


class CredentialError(ProfileError):
    """The credential store could not be read or written (code ``credentials_unavailable``),
    such as a locked keychain."""


class Keyring(Protocol):
    """What KeyringCredentials needs of a keyring backend."""

    def get_password(self, service: str, username: str, /) -> str | None: ...

    def set_password(self, service: str, username: str, password: str, /) -> None: ...

    def delete_password(self, service: str, username: str, /) -> None: ...


class CredentialStore(Protocol):
    def read(self, key: str) -> str | None:
        """The secret stored under ``key``; None when there is none."""
        ...

    def write(self, key: str, secret: str) -> None: ...

    def delete(self, key: str) -> None:
        """Remove the secret; nothing happens when there is none."""
        ...


def system_keyring() -> KeyringBackend:
    """The platform's own backend, named rather than discovered: keyring looks for backends
    through package entry points, which a frozen build may not carry."""
    if sys.platform == "darwin":
        from keyring.backends import macOS

        return macOS.Keyring()
    if sys.platform == "win32":
        from keyring.backends import Windows

        return Windows.WinVaultKeyring()
    import keyring

    return keyring.get_keyring()


class KeyringCredentials:
    """The system's credential store. It is opened at the first read or write, on the thread
    that makes it; the library makes them on the asyncio thread's workers."""

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service
        self._keyring: Keyring | None = None

    def _backend(self) -> Keyring:
        if self._keyring is None:
            self._keyring = system_keyring()
        return self._keyring

    def read(self, key: str) -> str | None:
        try:
            return self._backend().get_password(self._service, key)
        except KeyringError as error:
            raise CredentialError("credentials_unavailable", str(error)) from error

    def write(self, key: str, secret: str) -> None:
        try:
            self._backend().set_password(self._service, key, secret)
        except KeyringError as error:
            raise CredentialError("credentials_unavailable", str(error)) from error

    def delete(self, key: str) -> None:
        if self.read(key) is None:
            return
        try:
            self._backend().delete_password(self._service, key)
        except KeyringError as error:
            raise CredentialError("credentials_unavailable", str(error)) from error


class WarpAccounts:
    """The WARP account, one per device, as compact JSON under WARP_ACCOUNT. Windows keeps
    at most 2560 bytes a credential; an account takes well under half of that."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def load(self) -> WarpAccount | None:
        """The stored account; None when there is none, or it is not one this version reads."""
        text = self._store.read(WARP_ACCOUNT)
        if text is None:
            return None
        try:
            stored = json.loads(text)
            account = WarpAccount(**{f.name: stored[f.name] for f in fields(WarpAccount)})
        except ValueError, TypeError, KeyError:
            return None
        return account if account.protocol in WARP_PROTOCOLS else None

    def save(self, account: WarpAccount) -> None:
        self._store.write(WARP_ACCOUNT, json.dumps(asdict(account), separators=(",", ":")))

    def delete(self) -> None:
        self._store.delete(WARP_ACCOUNT)


class MeshSecrets:
    """A mesh network's secrets, as compact JSON under ``mesh:<network id>``: Tailscale's
    auth key, ZeroTier's identity, EasyTier's network secret."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def load(self, network_id: str) -> dict[str, str]:
        """The secrets by name; empty when there are none, or none this version reads."""
        text = self._store.read(MESH_SECRET.format(id=network_id))
        if text is None:
            return {}
        try:
            stored = json.loads(text)
        except ValueError:
            return {}
        if not isinstance(stored, dict):
            return {}
        return {k: v for k, v in stored.items() if k in MESH_SECRETS and isinstance(v, str) and v}

    def save(self, network_id: str, secrets: dict[str, str]) -> None:
        """Keep the secrets that are set; with none, the stored ones go."""
        kept = {k: v for k, v in secrets.items() if v}
        if not kept:
            self.delete(network_id)
            return
        self._store.write(
            MESH_SECRET.format(id=network_id), json.dumps(kept, separators=(",", ":"))
        )

    def delete(self, network_id: str) -> None:
        self._store.delete(MESH_SECRET.format(id=network_id))
