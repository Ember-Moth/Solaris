"""Backup snapshots: what they hold, reading them back, and restoring them by replacing or
merging the local data (docs/storage.md §3)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from solaris.models.backup import (
    MAX_SNAPSHOT_BYTES,
    MERGE,
    REPLACE,
    BackupError,
    Backups,
    Counts,
    Snapshot,
    read_file,
    write_file,
)
from solaris.models.backup_auto import BackupTargets
from solaris.models.database import TOMBSTONE_DAYS
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import (
    KIND_FILE,
    KIND_SUBSCRIPTION,
    Profile,
    ProfileError,
    ProfileList,
    Usage,
)
from solaris.models.routing import (
    MODE_PLAN,
    MODE_PROFILE,
    Plan,
    PlanGroup,
    PlanRule,
    RoutingStore,
)
from solaris.models.settings import DatabaseSettingsStore, Settings
from solaris.models.store import ProfileStore
from solaris.models.subscription import Download
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.storage import stored

DAY = 86_400.0
LINK_A = "https://sub.example.com/api?token=secret-a"
LINK_B = "https://nodes.example.org/clash.yaml?token=secret-b"
DOWNLOADED = "proxies:\n  - {name: downloaded-node, type: ss, server: a.example.net, port: 1}\n"
LOCAL_CONFIG = (
    "proxies:\n  - {name: 家里, type: ss, server: home.example.net, port: 2}\n"
    "rules:\n  - MATCH,DIRECT\n"
)


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Machine:
    """One data directory, with a clock of its own."""

    def __init__(self, directory: Path, now: float = 1_000.0, device: str = "laptop") -> None:
        self.clock = Clock(now)
        self.store = ProfileStore(directory, clock=self.clock)
        self.routing = RoutingStore(self.store.database)
        self.settings = DatabaseSettingsStore(self.store.database)
        self.backups = Backups(self.store.database, device=device, app_version="1.0.0")
        self.directory = directory

    def at(self, now: float) -> Machine:
        self.clock.now = now
        return self

    def snapshot(self) -> Snapshot:
        return self.backups.snapshot()

    def profiles(self) -> ProfileList:
        return self.store.load()


SUB_A = Profile(
    id="a",
    name="订阅 A",
    kind=KIND_SUBSCRIPTION,
    url=LINK_A,
    auto_update_hours=12,
    updated_at=900.0,
    usage=Usage(1, 2, 300, 4_000),
    proxy_count=1,
)
SUB_B = Profile(id="b", name="订阅 B", kind=KIND_SUBSCRIPTION, url=LINK_B, in_pool=False)
FILE_C = Profile(id="c", name="家里", kind=KIND_FILE, path="/home/user/home.yaml")
PLAN = Plan(
    "plan-1",
    "日常",
    "proxy",
    (
        PlanGroup("proxy", "builtin", "select"),
        PlanGroup("auto", "builtin", "url-test"),
        PlanGroup("g-work", "custom", "select", "工作", {"regions": ["JP"]}, {"lazy": True}),
    ),
    (
        PlanRule("r-tg", "service", {"service": "telegram"}, "region:JP"),
        PlanRule("r-ip", "ip", {"items": ["10.0.0.0/8"], "no_resolve": True}, "DIRECT", False),
    ),
    {"warp": {"enabled": True, "via": "auto"}},
)


def populate(machine: Machine) -> None:
    """Three profiles, two overrides, a plan and some settings of each scope."""
    store = machine.store
    for profile, text in ((SUB_A, DOWNLOADED), (SUB_B, DOWNLOADED), (FILE_C, LOCAL_CONFIG)):
        store.write_config(profile.id, text)
    store.save(ProfileList((SUB_A, SUB_B, FILE_C), "a"))
    store.write_override(None, "mode: rule\n")
    store.write_override("b", "dns: {enable: true}\n")
    machine.routing.put_plan(PLAN)
    machine.routing.update_state(mode=MODE_PLAN, plan_id="plan-1", info_pattern="到期")
    machine.settings.update(language="en", update_check=False, mixed_port=7891, tun=True)


def plain(profiles: ProfileList) -> ProfileList:
    """The profiles without what came with their downloads, which no snapshot holds."""
    return replace(
        profiles,
        profiles=tuple(
            replace(p, updated_at=0.0, usage=None, proxy_count=None) for p in profiles.profiles
        ),
    )


# -- what a snapshot holds ---------------------------------------------------------------------
def test_a_snapshot_holds_the_users_data(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    document = machine.at(1_234.5).snapshot().document

    assert document["format"] == "solaris-backup"
    assert document["version"] == 1
    assert document["created_at"] == 1_234.5
    assert (document["device"], document["app_version"]) == ("laptop", "1.0.0")
    a, b, c = document["profiles"]
    assert (a["id"], a["url"], a["auto_update_hours"], a["in_pool"]) == ("a", LINK_A, 12, True)
    assert (b["id"], b["in_pool"]) == ("b", False)
    # A subscription is its link; a local file is its config.
    assert "config" not in a
    assert "config" not in b
    assert (c["kind"], c["path"], c["config"]["yaml"]) == ("file", FILE_C.path, LOCAL_CONFIG)
    assert {o["scope"]: o["yaml"] for o in document["overrides"]} == {
        "global": "mode: rule\n",
        "b": "dns: {enable: true}\n",
    }
    routing = document["routing"]
    assert (routing["mode"], routing["profile_id"], routing["plan_id"]) == (
        MODE_PLAN,
        "a",
        "plan-1",
    )
    assert routing["info_pattern"] == "到期"
    (plan,) = routing["plans"]
    assert (plan["id"], plan["name"], plan["fallback"]) == ("plan-1", "日常", "proxy")
    assert plan["egress"] == {"warp": {"enabled": True, "via": "auto"}}
    work = plan["groups"][2]
    assert (work["name"], work["conditions"], work["options"]) == (
        "工作",
        {"regions": ["JP"]},
        {"lazy": True},
    )
    assert [(r["id"], r["enabled"], r["content"]) for r in plan["rules"]] == [
        ("r-tg", True, {"service": "telegram"}),
        ("r-ip", False, {"items": ["10.0.0.0/8"], "no_resolve": True}),
    ]
    assert {s["key"]: s["value"] for s in document["settings"]} == {
        "language": "en",
        "update_check": False,
    }


def test_nothing_of_this_machine_goes_into_a_snapshot(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    with machine.store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO backup_targets (id, provider, account, created_at, updated_at) "
            "VALUES ('t', 'memory', 'someone@example.com', 1, 1)"
        )
        connection.execute("INSERT INTO state VALUES ('backup_digest:t', '\"token-ish\"', 1)")
    text = machine.snapshot().encode().decode()

    for absent in (
        "downloaded-node",  # what a subscription served
        "upload",
        "fetched_at",
        "proxy_count",
        "expire",
        "mixed_port",  # local settings
        "tun",
        "someone@example.com",  # the backup targets and this machine's state
        "token-ish",
    ):
        assert absent not in text, absent


def test_deleted_records_are_tombstones(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    machine.at(2_000)
    machine.store.save(ProfileList((SUB_A, FILE_C), "a"))  # without b
    machine.store.delete_config("b")
    machine.store.write_override(None, "")
    machine.routing.put_plan(Plan("plan-2", "临时", "proxy"))
    machine.routing.delete_plan("plan-2")
    machine.routing.put_plan(replace(PLAN, rules=PLAN.rules[:1]))

    document = machine.snapshot().document

    profiles = {p["id"]: p for p in document["profiles"]}
    assert profiles["b"] == {"id": "b", "updated_at": 2_000, "deleted_at": 2_000}
    overrides = {o["scope"]: o for o in document["overrides"]}
    assert overrides["global"] == {"scope": "global", "updated_at": 2_000, "deleted_at": 2_000}
    assert overrides["b"] == {"scope": "b", "updated_at": 2_000, "deleted_at": 2_000}
    plans = {p["id"]: p for p in document["routing"]["plans"]}
    assert plans["plan-2"] == {"id": "plan-2", "updated_at": 2_000, "deleted_at": 2_000}
    rules = {r["id"]: r for r in plans["plan-1"]["rules"]}
    assert rules["r-ip"] == {"id": "r-ip", "updated_at": 2_000, "deleted_at": 2_000}
    assert LINK_B not in machine.snapshot().encode().decode()
    assert machine.snapshot().info.counts == Counts(profiles=2, plans=1)


def test_the_digest_follows_the_content_only(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    first = machine.at(5_000).snapshot()
    later = machine.at(9_000).snapshot()
    assert first.document != later.document  # created_at
    assert first.digest == later.digest
    other = Backups(machine.store.database, device="desktop", app_version="1.1.0").snapshot()
    assert other.digest == first.digest

    # A subscription downloaded again changes this machine's cache, not the user's data.
    machine.store.write_config("a", DOWNLOADED + "# again\n")
    machine.store.save(ProfileList((replace(SUB_A, updated_at=9_000), SUB_B, FILE_C), "a"))
    machine.settings.update(mixed_port=7999)
    assert machine.snapshot().digest == first.digest

    machine.store.save(ProfileList((replace(SUB_A, name="改名"), SUB_B, FILE_C), "a"))
    assert machine.snapshot().digest != first.digest


# -- reading snapshots -------------------------------------------------------------------------
def test_round_trip_through_a_file(tmp_path: Path) -> None:
    machine = Machine(tmp_path / "data")
    populate(machine)
    snapshot = machine.snapshot()
    path = tmp_path / "backup.json"
    write_file(path, snapshot)
    assert read_file(path) == snapshot
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backup.json", "data"]
    assert "订阅 A" in path.read_text(encoding="utf-8")  # readable, not escaped


def document_of(tmp_path: Path) -> dict[str, Any]:
    machine = Machine(tmp_path)
    populate(machine)
    return machine.snapshot().document


def broken(document: dict[str, Any], change: str) -> dict[str, Any]:
    broken = json.loads(json.dumps(document))
    match change:
        case "format":
            broken["format"] = "clash"
        case "version":
            broken["version"] = "1"
        case "profiles":
            del broken["profiles"]
        case "in_pool":
            broken["profiles"][0]["in_pool"] = "yes"
        case "kind":
            broken["profiles"][0]["kind"] = "provider"
        case "twice":
            broken["profiles"][1]["id"] = broken["profiles"][0]["id"]
        case "config":
            broken["profiles"][2]["config"] = {"yaml": 1, "updated_at": 0}
        case "rule":
            broken["routing"]["plans"][0]["rules"][0]["kind"] = "lua"
        case "mode":
            broken["routing"]["mode"] = "global"
        case "setting":
            broken["settings"][0]["updated_at"] = -1
        case "tombstone":
            broken["overrides"][0] = {"scope": "global", "deleted_at": 5}
    return broken


@pytest.mark.parametrize(
    "change",
    [
        "format",
        "version",
        "profiles",
        "in_pool",
        "kind",
        "twice",
        "config",
        "rule",
        "mode",
        "setting",
        "tombstone",
    ],
)
def test_what_is_no_snapshot_is_refused(tmp_path: Path, change: str) -> None:
    data = json.dumps(broken(document_of(tmp_path), change)).encode()
    with pytest.raises(BackupError) as raised:
        Snapshot.decode(data)
    assert raised.value.code == "not_a_backup"


@pytest.mark.parametrize("data", [b"", b"\xff\xfe", b"[]", b"proxies: []\n", b'{"a": 1}'])
def test_other_files_are_refused(data: bytes) -> None:
    with pytest.raises(BackupError) as raised:
        Snapshot.decode(data)
    assert raised.value.code == "not_a_backup"


def test_a_newer_snapshot_is_refused(tmp_path: Path) -> None:
    document = document_of(tmp_path)
    document["version"] = 2
    with pytest.raises(BackupError) as raised:
        Snapshot.decode(json.dumps(document).encode())
    assert raised.value.code == "newer_backup"


def test_a_huge_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    with path.open("wb") as file:
        file.truncate(MAX_SNAPSHOT_BYTES + 1)
    with pytest.raises(BackupError) as raised:
        read_file(path)
    assert raised.value.code == "too_large"


# -- replace -----------------------------------------------------------------------------------
def test_replace_into_an_empty_database_restores_everything(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    target = Machine(tmp_path / "b", now=50_000)
    target.settings.update(mixed_port=8000)  # this machine's own

    target.backups.restore(source.snapshot(), REPLACE)

    assert plain(target.profiles()) == plain(replace(source.profiles(), has_global_override=True))
    assert target.store.read_config("c") == LOCAL_CONFIG
    with pytest.raises(Exception, match="config_missing"):
        target.store.read_config("a")  # downloaded again after a restore
    assert target.store.read_override(None) == "mode: rule\n"
    assert target.store.read_override("b") == "dns: {enable: true}\n"
    assert target.routing.plans() == source.routing.plans()
    assert target.routing.state() == source.routing.state()
    restored = DatabaseSettingsStore(target.store.database).current
    assert restored == Settings(language="en", update_check=False, mixed_port=8000)
    # New here, every record keeps its time: the same data gives the same snapshot.
    assert target.snapshot().digest == source.snapshot().digest


def test_replace_makes_this_machine_hold_what_the_snapshot_holds(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    snapshot = source.snapshot()
    target = Machine(tmp_path / "b", now=2_000)
    target.backups.restore(snapshot, REPLACE)

    # This machine goes its own way.
    target.at(3_000)
    own = Profile(id="x", name="本机的", kind=KIND_SUBSCRIPTION, url="https://x.example.com/s")
    target.store.write_config("x", DOWNLOADED)
    target.store.save(ProfileList((replace(SUB_A, name="改过"), own), "x"))
    target.store.write_override("x", "ipv6: false\n")
    target.routing.put_plan(Plan("plan-x", "本机方案", "DIRECT"))
    target.routing.put_plan(replace(PLAN, rules=(*PLAN.rules, PlanRule("r-x", "ip", {}, "DIRECT"))))
    target.routing.update_state(mode=MODE_PROFILE)
    target.settings.update(appearance="dark", language="zh")

    target.at(4_000).backups.restore(snapshot, REPLACE)

    assert plain(target.profiles()) == plain(replace(source.profiles(), has_global_override=True))
    assert target.store.read_override("x") == ""
    assert target.routing.plans() == source.routing.plans()
    assert target.routing.state() == source.routing.state()
    assert target.settings.reload() == Settings(language="en", update_check=False)
    # What the user had here is deleted now, and what the snapshot changed back is dated now.
    times = {
        row[0]: row[1:]
        for row in stored(target.directory, "SELECT id, updated_at, deleted_at FROM profiles")
    }
    assert times == {
        "a": (4_000, None),
        "b": (4_000, None),
        "c": (4_000, None),
        "x": (4_000, 4_000),
    }
    group = stored(target.directory, "SELECT updated_at FROM plan_groups WHERE id = 'g-work'")
    assert group == [(1_000,)]  # unchanged since it came
    assert stored(target.directory, "SELECT profile_id FROM profile_contents") == [("c",)]
    rule = stored(target.directory, "SELECT deleted_at FROM plan_rules WHERE id = 'r-x'")
    assert rule == [(4_000,)]
    appearance = stored(
        target.directory, "SELECT value, updated_at FROM settings WHERE key = ?", "appearance"
    )
    assert appearance == [('"system"', 4_000)]


def test_replace_brings_back_what_was_deleted(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    target = Machine(tmp_path / "b", now=2_000)
    target.backups.restore(source.snapshot(), REPLACE)
    target.at(3_000).store.save(ProfileList((SUB_A,), "a"))
    target.routing.delete_plan("plan-1")

    target.at(4_000).backups.restore(source.snapshot(), REPLACE)

    assert [p.id for p in target.profiles().profiles] == ["a", "b", "c"]
    assert [p.id for p in target.routing.plans()] == ["plan-1"]


# -- merge -------------------------------------------------------------------------------------
@pytest.fixture
def pair(tmp_path: Path) -> tuple[Machine, Machine]:
    """Two machines with the same data: the second restored the first's snapshot."""
    first = Machine(tmp_path / "a", device="laptop")
    populate(first)
    second = Machine(tmp_path / "b", now=1_500, device="desktop")
    second.backups.restore(first.snapshot(), REPLACE)
    return first, second


def test_merge_keeps_the_later_change_of_each_record(pair: tuple[Machine, Machine]) -> None:
    first, second = pair
    # The first machine renames a, edits a rule and the global override, adds a profile.
    first.at(2_000).store.save(ProfileList((replace(SUB_A, name="A 改名"), SUB_B, FILE_C), "a"))
    edited = replace(PLAN.rules[0], target="DIRECT")
    first.at(2_500).routing.put_plan(replace(PLAN, rules=(edited, PLAN.rules[1])))
    first.at(2_600).store.write_override(None, "mode: global\n")
    added = Profile(id="d", name="新订阅", kind=KIND_SUBSCRIPTION, url="https://d.example.com/s")
    first.store.save(ProfileList((replace(SUB_A, name="A 改名"), SUB_B, FILE_C, added), "a"))
    first.at(2_700).settings.update(appearance="dark")

    # The second machine, later, renames b, deletes the rule the first edited and c, and
    # changes the global override back.
    second.at(3_000).store.save(ProfileList((SUB_A, replace(SUB_B, name="B 改名")), "a"))
    second.store.delete_config("c")
    second.routing.put_plan(replace(PLAN, rules=(PLAN.rules[1],)))
    second.store.write_override(None, "mode: direct\n")
    second.settings.update(language="zh")

    second.at(4_000).backups.restore(first.snapshot(), MERGE)

    names = {p.id: p.name for p in second.profiles().profiles}
    assert names == {"a": "A 改名", "b": "B 改名", "d": "新订阅"}  # c's deletion is later
    (plan,) = second.routing.plans()
    assert [r.id for r in plan.rules] == ["r-ip"]  # the deletion is later than the edit
    assert second.store.read_override(None) == "mode: direct\n"
    assert second.store.read_override("b") == "dns: {enable: true}\n"
    assert second.settings.reload() == Settings(
        language="zh", appearance="dark", update_check=False
    )

    # Merged the other way round as well, both hold the same data.
    first.at(4_100).backups.restore(second.snapshot(), MERGE)
    assert first.snapshot().digest == second.snapshot().digest


def test_merge_takes_a_deletion_that_is_later(pair: tuple[Machine, Machine]) -> None:
    first, second = pair
    first.at(3_000).store.save(ProfileList((SUB_A,), "a"))
    first.store.delete_config("b")
    first.store.delete_config("c")
    first.routing.put_plan(Plan("plan-2", "第二套", "proxy"))
    first.at(3_100).routing.delete_plan("plan-1")
    second.at(2_000).store.save(ProfileList((SUB_A, replace(SUB_B, name="早一些"), FILE_C), "a"))

    second.at(4_000).backups.restore(first.snapshot(), MERGE)

    assert [p.id for p in second.profiles().profiles] == ["a"]
    assert [p.id for p in second.routing.plans()] == ["plan-2"]
    assert stored(second.directory, "SELECT profile_id FROM profile_contents") == []
    assert second.store.read_override("b") == ""  # deleted with b on the first machine


def test_merge_revives_what_was_changed_after_its_deletion(pair: tuple[Machine, Machine]) -> None:
    first, second = pair
    second.at(2_000).store.save(ProfileList((SUB_A, FILE_C), "a"))
    first.at(3_000).store.save(ProfileList((SUB_A, replace(SUB_B, name="还要用"), FILE_C), "a"))

    second.at(4_000).backups.restore(first.snapshot(), MERGE)

    assert second.profiles().get("b").name == "还要用"


def test_merge_keeps_the_config_read_later(pair: tuple[Machine, Machine]) -> None:
    first, second = pair
    first.at(2_000).store.write_config("c", LOCAL_CONFIG + "# first\n")
    second.at(3_000).store.write_config("c", LOCAL_CONFIG + "# second\n")
    second.at(4_000).backups.restore(first.snapshot(), MERGE)
    assert second.store.read_config("c").endswith("# second\n")
    first.at(4_000).backups.restore(second.snapshot(), MERGE)
    assert first.store.read_config("c").endswith("# second\n")


def test_merge_routing_state_and_new_records(pair: tuple[Machine, Machine]) -> None:
    first, second = pair
    first.at(2_000).routing.update_state(mode=MODE_PROFILE)
    second.at(1_800).routing.put_plan(Plan("plan-b", "只在第二台", "DIRECT"))
    second.backups.restore(first.snapshot(), MERGE)
    assert second.routing.state().mode == MODE_PROFILE
    assert [p.id for p in second.routing.plans()] == ["plan-1", "plan-b"]


def test_a_tombstone_of_something_never_here_adds_nothing(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    source.at(2_000).store.save(ProfileList((SUB_A,), "a"))
    target = Machine(tmp_path / "b", now=3_000)
    target.backups.restore(source.snapshot(), MERGE)
    assert stored(target.directory, "SELECT id FROM profiles") == [("a",)]


def test_settings_this_version_does_not_accept_are_left_out(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    document = source.snapshot().document
    document["settings"] += [
        {"key": "appearance", "value": "sepia", "updated_at": 5},
        {"key": "mixed_port", "value": 1234, "updated_at": 5},  # local
        {"key": "removed_setting", "value": 1, "updated_at": 5},
    ]
    target = Machine(tmp_path / "b")
    target.backups.restore(Snapshot.decode(json.dumps(document).encode()), REPLACE)
    assert target.settings.reload() == Settings(language="en", update_check=False)
    keys = stored(target.directory, "SELECT key FROM settings ORDER BY key")
    assert keys == [("language",), ("update_check",)]


def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        machine.backups.restore(machine.snapshot(), "overwrite")


# -- restoring through the library -------------------------------------------------------------
class HeldFetcher(SimulatedFetcher):
    """Serves the sample config once ``release`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    async def fetch(self, url: str) -> Download:
        await asyncio.to_thread(self.release.wait, 5)
        return await super().fetch(url)


def test_an_update_that_ends_during_a_restore_does_not_undo_it(tmp_path: Path) -> None:
    source = Machine(tmp_path / "a")
    populate(source)
    snapshot = source.snapshot()
    target = Machine(tmp_path / "b")
    seed_profiles(target.store, 1_000)  # sample-a, sample-b, sample-c
    fetcher = HeldFetcher()
    library = ProfileLibrary(target.store, SimulatedHost(), fetcher, clock=target.clock)

    def write() -> None:
        target.backups.restore(snapshot, REPLACE)
        # The download ends now; its update must wait until the restore is taken up.
        fetcher.release.set()
        time.sleep(0.2)

    async def scenario() -> ProfileList:
        update = asyncio.create_task(library.profiles.update("sample-a"))
        await asyncio.sleep(0.05)  # downloading
        restored = await library.profiles.restore(write)
        with pytest.raises(ProfileError):
            await update  # sample-a is gone with the restore
        return restored

    profiles = asyncio.run(scenario())
    assert [p.id for p in profiles.profiles] == ["a", "b", "c"]
    assert [p.id for p in target.profiles().profiles] == ["a", "b", "c"]


# -- tombstones past their time (storage.md §2) ------------------------------------------------
def delete_some(machine: Machine) -> None:
    """At 2 000: profile b with its override, a plan with its groups and rules, a rule of the
    plan that stays, and a backup target. At 3 000: the global override."""
    machine.at(2_000).store.save(ProfileList((SUB_A, FILE_C), "a"))
    machine.store.delete_config("b")
    group = PlanGroup("proxy", "builtin", "select")
    rule = PlanRule("r-2", "ip", {"items": []}, "DIRECT")
    machine.routing.put_plan(Plan("plan-2", "临时", "proxy", (group,), (rule,)))
    machine.routing.delete_plan("plan-2")
    machine.routing.put_plan(replace(PLAN, rules=PLAN.rules[:1]))
    targets = BackupTargets(machine.store.database)
    targets.remove(targets.add("memory", "someone").id)
    machine.at(3_000).store.write_override(None, "")


def reopened(machine: Machine, now: float) -> Machine:
    machine.store.close()
    return Machine(machine.directory, now=now)


def test_tombstones_stay_their_time(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    delete_some(machine)
    before = machine.snapshot()

    machine = reopened(machine, 2_000 + TOMBSTONE_DAYS * DAY)

    assert machine.snapshot().digest == before.digest
    assert stored(tmp_path, "SELECT id FROM profiles WHERE deleted_at IS NOT NULL") == [("b",)]
    assert stored(tmp_path, "SELECT COUNT(*) FROM backup_targets") == [(1,)]


def test_old_tombstones_are_purged_at_startup(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    populate(machine)
    delete_some(machine)
    live = (plain(machine.profiles()), machine.routing.plans(), machine.settings.current)

    machine = reopened(machine, 2_001 + TOMBSTONE_DAYS * DAY)

    assert stored(tmp_path, "SELECT id FROM profiles ORDER BY id") == [("a",), ("c",)]
    # The global override was deleted later than the rest.
    assert stored(tmp_path, "SELECT scope, deleted_at FROM overrides") == [("global", 3_000)]
    assert stored(tmp_path, "SELECT id FROM plans") == [("plan-1",)]
    # The deleted plan's groups and rules went with it.
    assert stored(tmp_path, "SELECT DISTINCT plan_id FROM plan_groups") == [("plan-1",)]
    assert stored(tmp_path, "SELECT id, plan_id FROM plan_rules") == [("r-tg", "plan-1")]
    assert stored(tmp_path, "SELECT COUNT(*) FROM backup_targets") == [(0,)]
    assert (plain(machine.profiles()), machine.routing.plans(), machine.settings.current) == live
    document = machine.snapshot().document
    assert [p["id"] for p in document["profiles"]] == ["a", "c"]
    assert [p["id"] for p in document["routing"]["plans"]] == ["plan-1"]


def test_a_merge_brings_back_what_was_deleted_before_the_tombstones_went(tmp_path: Path) -> None:
    """A merge cannot tell a record whose tombstone was purged from one this machine never
    had, and takes it as new."""
    machine = Machine(tmp_path)
    populate(machine)
    older = machine.snapshot()  # b, its override and the rule r-ip, before their deletion
    delete_some(machine)

    machine.at(4_000).backups.restore(older, MERGE)
    assert [p.id for p in machine.profiles().profiles] == ["a", "c"]  # the deletion is later

    machine = reopened(machine, 2_001 + TOMBSTONE_DAYS * DAY)
    machine.backups.restore(older, MERGE)

    assert {p.id for p in machine.profiles().profiles} == {"a", "b", "c"}
    assert machine.store.read_override("b") == "dns: {enable: true}\n"
    (plan,) = machine.routing.plans()
    assert [r.id for r in plan.rules] == ["r-tg", "r-ip"]
    # The global override's tombstone is younger, so its deletion still wins.
    assert machine.store.read_override(None) == ""
