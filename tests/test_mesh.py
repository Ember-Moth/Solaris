"""The mesh networks added in the app (docs/mesh.md §10.2): their rows, their secrets in the
credential store, and what of them a backup holds."""

import json
from pathlib import Path

import pytest

from solaris.host.base import (
    EasyTierSettings,
    MeshNetwork,
    TailscaleSettings,
    ZeroTierSettings,
)
from solaris.models import mesh
from solaris.models.backup import MERGE, REPLACE, Backups, Snapshot
from solaris.models.credentials import CredentialError, MeshSecrets
from solaris.models.mesh import MeshStore
from solaris.models.store import ProfileStore
from tests.support.credentials import MemoryCredentials

HOME = MeshNetwork(
    "net-home", "家里", TailscaleSettings("tskey-auth-secret", "", "laptop"), via="proxy"
)
OFFICE = MeshNetwork("net-office", "公司", ZeroTierSettings("8056c2e21c000001", "id-secret"))
LAB = MeshNetwork(
    "net-lab",
    "实验室",
    EasyTierSettings("lab", "net-secret", ("tcp://a.example:11010", "udp://b.example:11010")),
)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def store_at(directory: Path, clock: Clock) -> tuple[ProfileStore, MeshStore]:
    profiles = ProfileStore(directory, clock=clock)
    return profiles, MeshStore(profiles.database)


def test_rows_keep_everything_but_the_secrets(tmp_path: Path) -> None:
    _, store = store_at(tmp_path, Clock(1_000))
    for network in (HOME, OFFICE, LAB):
        store.put(network)
    assert store.networks() == tuple(mesh.without_secrets(n) for n in (HOME, OFFICE, LAB))
    data = (tmp_path / "solaris.db").read_bytes()
    for secret in (b"tskey-auth-secret", b"id-secret", b"net-secret"):
        assert secret not in data
    # A change keeps the place; a deletion is a mark.
    store.put(MeshNetwork(HOME.id, "家 2", HOME.settings))
    store.delete(OFFICE.id)
    assert [n.name for n in store.networks()] == ["家 2", "实验室"]


def test_a_kind_this_version_does_not_know_is_left_out(tmp_path: Path) -> None:
    profiles, store = store_at(tmp_path, Clock(1_000))
    store.put(LAB)
    with profiles.database.transaction() as connection:
        connection.execute("UPDATE mesh_networks SET kind = 'wireguard-mesh'")
    assert store.networks() == ()


def test_secrets_round_trip_through_the_credential_store() -> None:
    credentials = MemoryCredentials()
    secrets = MeshSecrets(credentials)
    secrets.save(HOME.id, mesh.secrets_of(HOME))
    assert json.loads(credentials.secrets["mesh:net-home"]) == {"auth_key": "tskey-auth-secret"}
    assert secrets.load(HOME.id) == {"auth_key": "tskey-auth-secret"}
    assert mesh.with_secrets(mesh.without_secrets(HOME), secrets.load(HOME.id)) == HOME
    # No secret left: nothing stays stored.
    secrets.save(HOME.id, {"auth_key": ""})
    assert credentials.secrets == {}
    assert secrets.load(HOME.id) == {}
    # What this version cannot read counts as none.
    credentials.secrets["mesh:x"] = "not json"
    assert secrets.load("x") == {}
    credentials.secrets["mesh:x"] = json.dumps({"auth_key": 1, "other": "x"})
    assert secrets.load("x") == {}
    credentials.fail = True
    with pytest.raises(CredentialError):
        secrets.load(HOME.id)


def test_an_easytier_network_runs_with_its_secret_only() -> None:
    assert mesh.runnable(LAB)
    assert not mesh.runnable(mesh.without_secrets(LAB))
    assert mesh.runnable(mesh.without_secrets(HOME))  # logs in through the browser
    assert mesh.runnable(mesh.without_secrets(OFFICE))  # makes an identity of its own


def test_a_backup_holds_the_networks_without_their_secrets(tmp_path: Path) -> None:
    profiles, store = store_at(tmp_path / "a", Clock(1_000))
    store.put(HOME)
    store.put(LAB)
    store.delete(LAB.id)
    snapshot = Backups(profiles.database, device="laptop", app_version="0.1.0").snapshot()
    home, lab = snapshot.document["mesh"]
    assert home == {
        "id": "net-home",
        "kind": "tailscale",
        "name": "家里",
        "via": "proxy",
        "settings": {"control_url": "", "hostname": "laptop"},
        "position": 0,
        "created_at": 1_000,
        "updated_at": 1_000,
    }
    assert lab == {"id": "net-lab", "updated_at": 1_000, "deleted_at": 1_000}
    text = snapshot.encode().decode()
    assert "tskey-auth-secret" not in text
    assert "net-secret" not in text

    # Restored elsewhere: the network is back, waiting for its secrets.
    target_profiles, target = store_at(tmp_path / "b", Clock(5_000))
    backups = Backups(target_profiles.database, device="desktop", app_version="0.1.0")
    backups.restore(Snapshot.decode(snapshot.encode()), REPLACE)
    assert target.networks() == (mesh.without_secrets(HOME),)
    # A tombstone for what this machine never had leaves nothing (storage.md §3).
    assert backups.snapshot().document["mesh"] == [home]


def test_merging_keeps_the_later_network(tmp_path: Path) -> None:
    profiles, store = store_at(tmp_path / "a", Clock(1_000))
    store.put(HOME)
    snapshot = Backups(profiles.database, device="laptop", app_version="0.1.0").snapshot()
    clock = Clock(2_000)
    target_profiles, target = store_at(tmp_path / "b", clock)
    target.put(OFFICE)
    target.put(MeshNetwork(HOME.id, "改过的家", HOME.settings))
    Backups(target_profiles.database, device="desktop", app_version="0.1.0").restore(
        snapshot, MERGE
    )
    assert [n.name for n in target.networks()] == ["公司", "改过的家"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("mesh"),
        lambda d: d["mesh"].append({"id": "x", "kind": "wireguard-mesh"}),
        lambda d: d["mesh"].append(
            {
                "id": "x",
                "kind": "tailscale",
                "name": "x",
                "via": "",
                "settings": [],
                "position": 0,
                "created_at": 1,
                "updated_at": 1,
            }
        ),
    ],
)
def test_a_snapshot_with_broken_networks_is_refused(tmp_path: Path, change: object) -> None:
    profiles, store = store_at(tmp_path, Clock(1_000))
    store.put(HOME)
    document = Backups(profiles.database, device="laptop", app_version="0.1.0").snapshot().document
    change(document)  # type: ignore[operator]
    with pytest.raises(Exception, match="not_a_backup"):
        Snapshot.decode(json.dumps(document).encode())
