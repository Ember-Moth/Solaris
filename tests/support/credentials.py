"""Credential stores in memory, so the tests never touch the system's keychain or
Credential Manager."""

from __future__ import annotations

from solaris.models.credentials import CredentialError


class MemoryKeyring:
    """A keyring backend in memory; tests/conftest.py makes it the system's."""

    def __init__(self) -> None:
        self.secrets: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str, /) -> str | None:
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str, /) -> None:
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str, /) -> None:
        del self.secrets[(service, username)]


class MemoryCredentials:
    """A CredentialStore in memory; set ``fail`` to have every call fail as a locked
    keychain does."""

    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise CredentialError("credentials_unavailable", "the keychain is locked")

    def read(self, key: str) -> str | None:
        self._check()
        return self.secrets.get(key)

    def write(self, key: str, secret: str) -> None:
        self._check()
        self.secrets[key] = secret

    def delete(self, key: str) -> None:
        self._check()
        self.secrets.pop(key, None)
