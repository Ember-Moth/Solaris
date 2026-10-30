"""The credential store (decision D14) and the WARP account in it (docs/exits.md §3.2)."""

import pytest

from solaris.host.base import WarpAccount
from solaris.models.credentials import (
    SERVICE,
    WARP_ACCOUNT,
    CredentialError,
    KeyringCredentials,
    WarpAccounts,
)
from tests.support.credentials import MemoryCredentials, MemoryKeyring

ACCOUNT = WarpAccount(
    device_id="a1b2c3d4-0000-4000-8000-000000000000",
    token="token",
    protocol="masque-h2",
    private_key="private",
    peer_public_key="peer",
    endpoint="162.159.198.1:443",
    address_v4="172.16.0.2",
    address_v6="2606:4700:110:8a36::2",
    client_id="AAAA",
    registered_at_unix_ms=1_793_318_400_000,
)


def test_the_account_round_trips() -> None:
    store = MemoryCredentials()
    accounts = WarpAccounts(store)
    assert accounts.load() is None
    accounts.save(ACCOUNT)
    assert accounts.load() == ACCOUNT
    assert len(store.secrets[WARP_ACCOUNT].encode()) < 1280  # Windows keeps 2560 bytes
    accounts.delete()
    assert accounts.load() is None
    accounts.delete()  # nothing to delete


@pytest.mark.parametrize("text", ["not json", "{}", '{"device_id": 1}'])
def test_an_account_this_version_cannot_read_is_none(text: str) -> None:
    store = MemoryCredentials()
    store.secrets[WARP_ACCOUNT] = text
    assert WarpAccounts(store).load() is None


def test_an_unknown_protocol_is_none() -> None:
    store = MemoryCredentials()
    accounts = WarpAccounts(store)
    accounts.save(ACCOUNT)
    store.secrets[WARP_ACCOUNT] = store.secrets[WARP_ACCOUNT].replace("masque-h2", "quic")
    assert accounts.load() is None


def test_the_system_store_goes_through_keyring(keyring: MemoryKeyring) -> None:
    """tests/conftest.py puts a keyring in memory where the system's would be."""
    store = KeyringCredentials()
    store.write("k", "secret")
    assert keyring.secrets == {(SERVICE, "k"): "secret"}
    assert store.read("k") == "secret"
    store.delete("k")
    store.delete("k")
    assert store.read("k") is None


def test_keyring_failures_become_credential_errors(keyring: MemoryKeyring) -> None:
    from keyring.errors import KeyringLocked

    def locked(*_: object) -> None:
        raise KeyringLocked("the keychain is locked")

    keyring.set_password = locked  # type: ignore[method-assign]
    with pytest.raises(CredentialError) as raised:
        KeyringCredentials().write("k", "secret")
    assert raised.value.code == "credentials_unavailable"
