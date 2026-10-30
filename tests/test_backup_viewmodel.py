"""The settings page's 备份与同步 group: exporting a backup file and restoring one, through the
ViewModels with the simulated host and subscriptions served on this machine (the M7
acceptance of docs/roadmap.md, without a cloud account)."""

# The UI copy asserted here uses fullwidth punctuation, as the ViewModels write it.
# ruff: noqa: RUF001

from __future__ import annotations

import json
import threading
from collections.abc import Coroutine, Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt
from pytestqt.qtbot import QtBot

from solaris.models.backup import device_name
from solaris.models.routing import MODE_PLAN, PlanRule
from solaris.models.store import ProfileStore
from solaris.models.subscription import UrlFetcher
from solaris.viewmodels.backup import BackupViewModel
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.releases import offline_updater
from tests.support.simulated import SAMPLE_CONFIG, SimulatedHost
from tests.support.window import Loaded, texts

LOCAL_CONFIG = (
    "proxies:\n  - {name: 家里 01, type: ss, server: home.example.net, port: 2}\n"
    "rules:\n  - MATCH,DIRECT\n"
)
# Rules that name a group the config lacks: the host refuses it.
BROKEN_CONFIG = SAMPLE_CONFIG + "\n# broken\n" + "rules:\n  - MATCH,不存在的组\n"


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


class Subscriptions:
    """Subscriptions served on 127.0.0.1: ``/<name>`` answers with ``bodies[name]``."""

    def __init__(self) -> None:
        self.bodies: dict[str, str] = {}
        self.requests: list[str] = []
        served = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                name = self.path.lstrip("/")
                served.requests.append(name)
                body = served.bodies.get(name)
                if body is None:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/yaml; charset=utf-8")
                self.send_header("Subscription-Userinfo", "upload=1; download=2; total=10")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self, name: str) -> str:
        return f"{self.base}/{name}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[Subscriptions]:
    # Reach the loopback server directly even if the machine has a proxy configured.
    monkeypatch.setenv("no_proxy", "*")
    subscriptions = Subscriptions()
    subscriptions.bodies = {"airport": SAMPLE_CONFIG, "nodes": SAMPLE_CONFIG}
    yield subscriptions
    subscriptions.close()


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Machine:
    """A data directory with the app's ViewModels on it."""

    def __init__(self, directory: Path, clock: Clock) -> None:
        self.directory = directory
        self.clock = clock
        self.host = SimulatedHost(clock=clock)
        self.store = ProfileStore(directory, clock=clock)
        self.vms: ViewModels = compose(
            self.host, self.store, UrlFetcher(), clock, updater=offline_updater(directory)
        )
        self.vms.start()

    @property
    def backup(self) -> BackupViewModel:
        return self.vms.backup

    def call[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run a library call on the app's asyncio thread and wait for it."""
        return self.vms.runner.submit(coroutine).result(timeout=30)

    def close(self) -> None:
        self.vms.shutdown()


@pytest.fixture
def machines(qtbot: QtBot) -> Iterator[list[Machine]]:
    """The machines a test makes, shut down afterwards."""
    made: list[Machine] = []
    yield made
    for machine in made:
        machine.close()


def set_up(machine: Machine, served: Subscriptions, tmp_path: Path) -> None:
    """Two subscriptions and a local file, overrides, the default plan with a rule of the
    user's in use, and synced settings."""
    library = machine.vms.library
    machine.call(library.import_url(served.url("airport"), "机场"))
    machine.call(library.import_url(served.url("nodes"), "节点", auto_update=False))
    local = tmp_path / "home.yaml"
    local.write_bytes(LOCAL_CONFIG.encode())  # write_text would turn \n into \r\n on Windows
    machine.call(library.import_file(local))
    machine.call(library.save_override(None, "mode: rule\n"))
    profiles = machine.call(library.current())
    machine.call(library.save_override(profiles.profiles[1].id, "ipv6: false\n"))
    routing = machine.call(library.set_mode(MODE_PLAN))
    plan = routing.plan()
    assert plan is not None
    rule = PlanRule("r-home", "domain", {"items": ["home.example.net"]}, "DIRECT")
    machine.call(library.put_plan(replace(plan, rules=(rule, *plan.rules))))
    machine.vms.settings_store.update(appearance="dark", update_check_hours=72, mixed_port=7999)


def export(qtbot: QtBot, machine: Machine, path: Path) -> None:
    machine.backup.exportBackup(path.as_uri())
    assert prop(machine.backup, "busy")
    qtbot.waitUntil(lambda: not prop(machine.backup, "busy"), timeout=10_000)
    assert prop(machine.backup, "exportStatus") == f"已导出到 {path.name}"
    assert not prop(machine.backup, "exportFailed")


def wait_restored(qtbot: QtBot, machine: Machine) -> str:
    qtbot.waitUntil(lambda: not prop(machine.backup, "busy"), timeout=30_000)
    return prop(machine.backup, "restoreStatus")


def library_data(machine: Machine) -> dict[str, Any]:
    """What a restore must bring back, without the ids and times of this machine."""
    library = machine.vms.library
    profiles = machine.call(library.current())
    routing = machine.call(library.routing())
    store = machine.store
    return {
        "profiles": [
            (p.name, p.kind, p.url, p.auto_update_hours, p.in_pool, p.has_override)
            for p in profiles.profiles
        ],
        "active": profiles.active,
        "global": store.read_override(None),
        "own": [store.read_override(p.id) for p in profiles.profiles],
        "mode": routing.mode,
        "plans": routing.plans,
        "plan_in_use": routing.state.plan_id,
        "settings": (
            machine.vms.settings_store.current.appearance,
            machine.vms.settings_store.current.update_check_hours,
        ),
    }


def test_export_then_restore_on_an_empty_machine(
    qtbot: QtBot, machines: list[Machine], served: Subscriptions, tmp_path: Path
) -> None:
    clock = Clock(1_800_000_000.0)
    first = Machine(tmp_path / "first", clock)
    machines.append(first)
    set_up(first, served, tmp_path)
    backup = tmp_path / "solaris-backup.json"
    assert prop(first.backup, "exportStatus") == "不加密，其中有订阅链接"
    export(qtbot, first, backup)
    document = json.loads(backup.read_text(encoding="utf-8"))
    assert [p["kind"] for p in document["profiles"]] == ["subscription", "subscription", "file"]
    assert "proxies" not in json.dumps(document["profiles"][0])  # no download inside

    clock.now += 3600
    second = Machine(tmp_path / "second", clock)
    machines.append(second)
    served.requests.clear()
    served.bodies["nodes"] = BROKEN_CONFIG  # the second subscription went bad meanwhile
    profiles_changed: list[bool] = []
    second.vms.profiles.changed.connect(lambda: profiles_changed.append(True))

    second.backup.openBackup(backup.as_uri())  # no profiles here: restored as it is

    status = wait_restored(qtbot, second)
    assert status == "已恢复；1 份订阅更新失败，已在订阅页标出"
    assert not prop(second.backup, "restoreFailed")
    assert sorted(served.requests) == ["airport", "nodes"]  # downloaded again
    expected = library_data(first)
    restored = library_data(second)
    assert restored == expected
    assert second.vms.settings_store.current.mixed_port == 7890  # this machine's own

    library = second.vms.library
    profiles = second.call(library.current())
    airport, nodes, home = profiles.profiles
    assert profiles.update_failed == {nodes.id}  # marked, and kept
    store = second.store
    assert store.read_config(airport.id) == SAMPLE_CONFIG
    assert airport.usage is not None
    assert store.read_config(home.id) == LOCAL_CONFIG  # from the backup; the path is not read
    # The tunnel runs the restored plan; the bad subscription adds no nodes to it.
    assert second.host.config is not None
    assert second.host.config.composition is not None
    sources = [source.name for source in second.host.config.composition.sources]
    assert sources == ["机场", "home"]
    # The other pages show what came back.
    qtbot.waitUntil(lambda: bool(profiles_changed))
    rows = prop(second.vms.profiles, "model").rows
    assert [row.updateFailed for row in rows] == [False, True, False]


def test_restore_asks_on_a_machine_with_profiles(
    qtbot: QtBot, machines: list[Machine], served: Subscriptions, tmp_path: Path
) -> None:
    clock = Clock(1_800_000_000.0)
    first = Machine(tmp_path / "first", clock)
    machines.append(first)
    set_up(first, served, tmp_path)
    backup = tmp_path / "first.json"
    export(qtbot, first, backup)

    second = Machine(tmp_path / "second", clock)
    machines.append(second)
    second.call(second.vms.library.import_url(served.url("airport"), "本机的订阅"))
    asked: list[bool] = []
    second.backup.restoreRequested.connect(lambda: asked.append(True))

    second.backup.openBackup(backup.as_uri())
    qtbot.waitUntil(lambda: bool(asked))
    assert not prop(second.backup, "busy")
    assert prop(second.backup, "snapshotDetail") == "3 份订阅、1 套分流方案"
    assert prop(second.backup, "localSummary") == "本机已有 1 份订阅、0 套分流方案"
    assert prop(second.backup, "snapshotTitle").startswith(device_name() + " · ")
    assert [m["value"] for m in prop(second.backup, "restoreModes")] == ["merge", "replace"]
    assert prop(second.backup, "restoreMode") == "merge"

    second.backup.cancelRestore()
    assert prop(second.backup, "localSummary") == ""
    second.backup.confirmRestore()  # nothing pending any more
    assert not prop(second.backup, "busy")

    second.backup.openBackup(backup.as_uri())
    qtbot.waitUntil(lambda: len(asked) == 2)
    second.backup.setRestoreMode("replace")
    second.backup.confirmRestore()
    assert wait_restored(qtbot, second) == "已从 first.json 恢复"
    names = [p.name for p in second.call(second.vms.library.current()).profiles]
    assert names == ["机场", "节点", "home"]  # replaced: this machine's own is gone


def test_merge_keeps_the_later_changes(
    qtbot: QtBot, machines: list[Machine], served: Subscriptions, tmp_path: Path
) -> None:
    clock = Clock(1_800_000_000.0)
    first = Machine(tmp_path / "first", clock)
    machines.append(first)
    set_up(first, served, tmp_path)
    export(qtbot, first, tmp_path / "base.json")
    second = Machine(tmp_path / "second", clock)
    machines.append(second)
    clock.now += 60
    second.backup.openBackup((tmp_path / "base.json").as_uri())
    wait_restored(qtbot, second)

    # Each machine changes a part; where both change the same record, the second is later.
    lib1, lib2 = first.vms.library, second.vms.library
    clock.now += 60
    nodes = first.call(lib1.current()).profiles[1]
    first.call(lib1.set_auto_update(nodes.id, True))
    plan = first.call(lib1.routing()).plan()
    assert plan is not None
    rule = replace(plan.rules[0], target="REJECT")
    first.call(lib1.put_plan(replace(plan, rules=(rule, *plan.rules[1:]))))
    first.vms.settings_store.update(appearance="light")
    clock.now += 60
    plan = second.call(lib2.routing()).plan()
    assert plan is not None
    rule = replace(plan.rules[0], target="proxy")
    second.call(lib2.put_plan(replace(plan, rules=(rule, *plan.rules[1:]))))
    second.call(lib2.save_override(None, "mode: rule\nlog-level: info\n"))
    export(qtbot, second, tmp_path / "second.json")

    clock.now += 60
    first.backup.openBackup((tmp_path / "second.json").as_uri())
    qtbot.waitUntil(lambda: prop(first.backup, "localSummary") != "")
    first.backup.confirmRestore()  # merge, the default
    assert wait_restored(qtbot, first) == "已从 second.json 恢复"

    merged = first.call(lib1.current())
    assert merged.get(nodes.id).auto_update_hours == 12  # only the first changed it
    plan = first.call(lib1.routing()).plan()
    assert plan is not None
    assert plan.rules[0].target == "proxy"  # the second's edit is later
    assert first.store.read_override(None) == "mode: rule\nlog-level: info\n"
    assert first.vms.settings_store.current.appearance == "light"  # only the first changed it


def test_files_that_are_no_backup(qtbot: QtBot, machines: list[Machine], tmp_path: Path) -> None:
    machine = Machine(tmp_path / "data", Clock(1_800_000_000.0))
    machines.append(machine)
    bad = tmp_path / "config.yaml"
    bad.write_text(SAMPLE_CONFIG, encoding="utf-8")
    for path, text in (
        (bad, "无法恢复：这不是 Solaris 的备份文件"),
        (tmp_path / "missing.json", "无法恢复：No such file or directory"),
    ):
        machine.backup.openBackup(path.as_uri())
        assert wait_restored(qtbot, machine) == text
        assert prop(machine.backup, "restoreFailed")

    machine.backup.exportBackup((tmp_path / "missing" / "b.json").as_uri())
    qtbot.waitUntil(lambda: not prop(machine.backup, "busy"))
    assert prop(machine.backup, "exportFailed")
    assert prop(machine.backup, "exportStatus").startswith("导出失败：")


# -- the settings page -------------------------------------------------------------------------
def test_the_group_and_the_restore_dialog(qtbot: QtBot, loaded: Loaded, tmp_path: Path) -> None:
    loaded.view_models.shell.setPage("settings")
    shown = texts(loaded.window.contentItem())
    assert {"备份与同步", "导出到文件", "从文件恢复", "不加密，其中有订阅链接"} <= shown
    assert not {"iCloud", "Google Drive"} & shown  # not until their providers come

    backup = loaded.view_models.backup
    path = tmp_path / "backup.json"
    backup.exportBackup(path.as_uri())
    qtbot.waitUntil(lambda: not prop(backup, "busy"))

    def ask() -> None:
        backup.openBackup(path.as_uri())  # the seeded profiles are here: it asks
        qtbot.waitUntil(lambda: loaded.focused_text() == "恢复")  # the default button

    ask()
    shown = {t for item in loaded.overlay_items(lambda i: True) for t in texts(item)}
    assert {"从文件恢复", "合并", "替换", "取消", "本机已有 3 份订阅、0 套分流方案"} <= shown
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert prop(backup, "localSummary") == ""  # cancelled
    ask()
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(
        lambda: prop(backup, "restoreStatus") == "已从 backup.json 恢复", timeout=10_000
    )
    assert loaded.warnings == []
