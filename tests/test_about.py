"""The settings page's 关于 group: updates against a fake GitHub, the automatic checks, the
diagnostics archive and the license list (docs/release.md)."""

# The UI copy asserted here uses fullwidth punctuation, as the ViewModels write it.
# ruff: noqa: RUF001

from __future__ import annotations

import json
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from solaris import diagnostics
from solaris.host.base import HostError, HostInfo, HostStatus, LogEntry
from solaris.models.routing import Plan, PlanRule, RoutingStore
from solaris.models.settings import Settings
from solaris.models.store import ProfileStore
from solaris.update.releases import ReleaseSource, UpdateError
from solaris.update.updater import Check, Downloaded, Updater
from solaris.viewmodels import about as about_module
from solaris.viewmodels.about import AboutViewModel
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.releases import FakeGitHub, FakeVerifier, fake_github, offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

NOW = datetime(2026, 10, 30, 9).timestamp()
DMG = "Solaris-0.2.0-macos-arm64.dmg"
EXE = "Solaris-0.2.0-windows-x64-setup.exe"
SECRET_OVERRIDE = "prepend-rules:\n  - DOMAIN,override-secret.example.net,DIRECT\n"
SECRET_RULE = "plan-secret.example.net"


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with fake_github() as server:
        server.add(DMG, b"disk image " * 50_000)
        server.add(EXE, b"MZ" + b"installer " * 50_000)
        server.add_checksums(DMG, EXE)
        yield server


@pytest.fixture
def built(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    view_models = compose(
        SimulatedHost(),
        store,
        SimulatedFetcher(),
        lambda: NOW,
        updater=offline_updater(tmp_path),
    )
    view_models.runner.start()
    yield view_models
    view_models.shutdown()


class Opened:
    def __init__(self, result: bool = True) -> None:
        self.paths: list[Path] = []
        self.result = result

    def __call__(self, path: Path) -> bool:
        self.paths.append(path)
        return self.result


def about_for(
    built: ViewModels,
    updater: Updater,
    opener: Opened | None = None,
    logs: list[LogEntry] | None = None,
    **options: Any,
) -> AboutViewModel:
    return AboutViewModel(
        updater,
        built.settings_store,
        built.host,
        built.library,
        built.runner,
        lambda: logs or [],
        clock=lambda: NOW,
        opener=opener or Opened(),
        **options,
    )


def updater_for(
    github: FakeGitHub,
    tmp_path: Path,
    verifier: FakeVerifier | None = None,
    *,
    current: str = "0.1.0",
    system: str = "darwin",
    machine: str = "arm64",
) -> Updater:
    return Updater(
        tmp_path / "data",
        ReleaseSource(github.latest_url),
        verifier or FakeVerifier(),
        current=current,
        system=system,
        machine=machine,
    )


def settle(qtbot: QtBot, about: AboutViewModel, *states: str) -> None:
    qtbot.waitUntil(lambda: prop(about, "updateState") in states, timeout=5000)


# -- update ------------------------------------------------------------------------------------


def test_update_from_check_to_install(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    opened = Opened()
    about = about_for(built, updater_for(github, tmp_path), opened)
    assert prop(about, "version") == "0.1.0"
    assert prop(about, "updateButton") == "检查更新"
    assert prop(about, "updateStatus") == ""

    about.runUpdateAction()
    assert prop(about, "updateButton") == "正在检查…"
    assert not prop(about, "updateButtonEnabled")
    settle(qtbot, about, "available")
    assert prop(about, "updateStatus") == "有新版本 0.2.0"
    assert prop(about, "updateButton") == "下载"
    assert prop(about, "releaseUrl").endswith("/releases/tag/v0.2.0")

    about.runUpdateAction()
    settle(qtbot, about, "ready")
    assert prop(about, "updateStatus") == "0.2.0 已下载并通过校验"
    assert prop(about, "updateButton") == "打开安装包…"
    assert prop(about, "downloadProgress") == 1.0

    with qtbot.waitSignal(about.installRequested):
        about.runUpdateAction()
    assert opened.paths == []  # nothing opens before the user confirms
    assert prop(about, "installTitle") == "打开 Solaris 0.2.0 的安装包？"
    assert "访达" in prop(about, "installMessage")
    about.confirmInstall()
    qtbot.waitUntil(lambda: bool(opened.paths))
    assert opened.paths == [tmp_path / "data" / "updates" / DMG]
    assert prop(about, "updateState") == "ready"


def test_windows_says_what_its_installer_does(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    about = about_for(built, updater_for(github, tmp_path, system="win32", machine="AMD64"))
    about.checkForUpdates()
    settle(qtbot, about, "available")
    assert "管理员权限" in prop(about, "installMessage")


def test_already_current(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    about = about_for(built, updater_for(github, tmp_path, current="0.2.0"))
    about.checkForUpdates()
    settle(qtbot, about, "latest")
    assert prop(about, "updateStatus") == "已是最新版本 · 刚刚检查"
    assert prop(about, "updateButton") == "检查更新"
    assert prop(about, "releaseUrl") == ""


def test_nothing_released_yet(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    github.status = 404
    about = about_for(built, updater_for(github, tmp_path))
    about.checkForUpdates()
    settle(qtbot, about, "latest")
    assert not prop(about, "updateFailed")


def test_no_installer_for_this_machine(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    about = about_for(built, updater_for(github, tmp_path, system="linux", machine="x86_64"))
    about.checkForUpdates()
    settle(qtbot, about, "unavailable")
    assert prop(about, "updateStatus") == "有新版本 0.2.0 · 这个版本没有适合本机的安装包"
    assert prop(about, "updateButton") == "检查更新"
    assert prop(about, "releaseUrl")  # the notes are still worth reading


@pytest.mark.parametrize(
    ("setup", "status"),
    [
        (lambda github: setattr(github, "status", 500), "检查更新失败：HTTP 500"),
        (lambda github: setattr(github, "tag", "nightly"), "发布的版本号无法识别"),
    ],
)
def test_check_failures(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path, setup: Any, status: str
) -> None:
    setup(github)
    about = about_for(built, updater_for(github, tmp_path))
    about.checkForUpdates()
    settle(qtbot, about, "failed")
    assert prop(about, "updateFailed")
    assert prop(about, "updateStatus") == status
    assert prop(about, "updateButton") == "检查更新"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (UpdateError("signer_mismatch", "TeamIdentifier=X"), "安装包不是由 Solaris 的开发者签名的"),
        (
            UpdateError("signature_invalid", "code object is not signed at all"),
            "安装包的签名无效：code object is not signed at all",
        ),
    ],
)
def test_rejected_signature(
    qtbot: QtBot,
    built: ViewModels,
    github: FakeGitHub,
    tmp_path: Path,
    error: UpdateError,
    status: str,
) -> None:
    update = updater_for(github, tmp_path, FakeVerifier(error))
    about = about_for(built, update)
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    settle(qtbot, about, "failed")
    assert prop(about, "updateStatus") == status
    assert list(update.directory.iterdir()) == []


def test_checksum_mismatch(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    github.add_checksums(DMG, wrong=DMG)
    about = about_for(built, updater_for(github, tmp_path))
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    settle(qtbot, about, "failed")
    assert prop(about, "updateStatus") == "下载的文件与校验和不符"


def test_a_swapped_installer_is_not_opened(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    opened = Opened()
    about = about_for(built, updater_for(github, tmp_path), opened)
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    settle(qtbot, about, "ready")
    (tmp_path / "data" / "updates" / DMG).write_bytes(b"something else")
    about.confirmInstall()
    settle(qtbot, about, "failed")
    assert prop(about, "updateStatus") == "下载的文件与校验和不符"
    assert opened.paths == []


def test_the_system_cannot_open_it(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    about = about_for(built, updater_for(github, tmp_path), Opened(result=False))
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    settle(qtbot, about, "ready")
    about.confirmInstall()
    settle(qtbot, about, "failed")
    assert prop(about, "updateStatus") == f"无法打开安装包：{DMG}"


class SlowDownload(Updater):
    """Waits in the download until it is cancelled."""

    started = threading.Event()

    def download(
        self, check: Check, progress: Any = None, cancelled: Any = None, verifying: Any = None
    ) -> Downloaded:
        progress(1, 100)
        self.started.set()
        cancelled.wait(5)
        raise UpdateError("cancelled")


def test_cancel_a_download(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    update = SlowDownload(
        tmp_path,
        ReleaseSource(github.latest_url),
        FakeVerifier(),
        current="0.1.0",
        system="darwin",
        machine="arm64",
    )
    about = about_for(built, update)
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    qtbot.waitUntil(lambda: prop(about, "updateStatus") == "正在下载 1%")
    assert prop(about, "updateButton") == "取消"
    about.runUpdateAction()
    settle(qtbot, about, "available")
    assert prop(about, "updateButton") == "下载"


def test_start_clears_old_downloads(qtbot: QtBot, built: ViewModels, tmp_path: Path) -> None:
    update = offline_updater(tmp_path / "data")
    update.directory.mkdir(parents=True)
    (update.directory / "Solaris-0.1.0-macos-arm64.dmg").write_bytes(b"old")
    built.settings_store.update(update_check=False)
    about = about_for(built, update)
    about.start()
    qtbot.waitUntil(lambda: not any(update.directory.iterdir()))
    about.stop()


# -- automatic checks --------------------------------------------------------------------------


def test_checks_at_startup_and_on_schedule(
    qtbot: QtBot,
    built: ViewModels,
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(about_module, "STARTUP_CHECK_MS", 50)
    monkeypatch.setattr(about_module, "HOUR_MS", 100)  # one "day" is 2.4 s
    about = about_for(built, updater_for(github, tmp_path))
    about.start()
    settle(qtbot, about, "available")
    first = len(github.requests)
    assert first >= 1
    qtbot.waitUntil(lambda: len(github.requests) > first, timeout=5000)
    about.stop()


def test_automatic_checks_follow_the_settings(
    qtbot: QtBot,
    built: ViewModels,
    github: FakeGitHub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(about_module, "STARTUP_CHECK_MS", 50)
    built.settings_store.update(update_check=False)
    about = about_for(built, updater_for(github, tmp_path))
    about.start()
    qtbot.wait(200)
    assert github.requests == []  # off: no check at startup
    assert not prop(about, "autoCheck")

    about.setAutoCheck(True)  # turning it on checks at once
    settle(qtbot, about, "available")
    assert built.settings_store.current.update_check

    assert [o["label"] for o in prop(about, "intervalOptions")] == ["每天", "每 3 天", "每周"]
    assert prop(about, "checkInterval") == "24"
    about.setCheckInterval("168")
    assert built.settings_store.current.update_check_hours == 168
    assert prop(about, "checkInterval") == "168"
    about.setCheckInterval("12")  # not a choice
    assert built.settings_store.current.update_check_hours == 168
    about.stop()


def test_an_automatic_check_leaves_a_download_alone(
    qtbot: QtBot, built: ViewModels, github: FakeGitHub, tmp_path: Path
) -> None:
    about = about_for(built, updater_for(github, tmp_path))
    about.checkForUpdates()
    settle(qtbot, about, "available")
    about.runUpdateAction()
    settle(qtbot, about, "ready")
    requests = len(github.requests)
    about._check_automatically()
    qtbot.wait(200)
    assert len(github.requests) == requests
    assert prop(about, "updateState") == "ready"


# -- diagnostics -------------------------------------------------------------------------------


def test_every_setting_is_classified() -> None:
    names = {item.name for item in fields(Settings)}
    assert names == diagnostics.SETTINGS_EXPORTED | diagnostics.SETTINGS_WITHHELD
    assert not diagnostics.SETTINGS_EXPORTED & diagnostics.SETTINGS_WITHHELD


def read_archive(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def test_export_diagnostics(qtbot: QtBot, tmp_path: Path) -> None:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    store.write_override(None, SECRET_OVERRIDE)
    store.write_override("sample-b", SECRET_OVERRIDE)
    plan = Plan(
        "plan-1",
        "我的私人方案",
        "proxy",
        rules=(PlanRule("rule-1", "domain", {"domains": [SECRET_RULE]}, "proxy"),),
    )
    RoutingStore(store.database).put_plan(plan)
    built = compose(
        SimulatedHost(), store, SimulatedFetcher(), lambda: NOW, updater=offline_updater(tmp_path)
    )
    built.runner.start()
    logs = [
        LogEntry("info", "[TCP] 127.0.0.1:5000 --> www.example.com:443 using Proxy", 1_000),
        LogEntry("warning", "dial failed", 2_000),
    ]
    about = about_for(built, offline_updater(tmp_path), logs=logs)
    assert prop(about, "diagnosticsName") == "solaris-diagnostics-20261030-090000.zip"
    target = tmp_path / "out" / "diag.zip"
    target.parent.mkdir()
    about.exportDiagnostics(target.as_uri())
    assert prop(about, "exporting")
    qtbot.waitUntil(lambda: not prop(about, "exporting"), timeout=5000)
    assert prop(about, "diagnosticsStatus") == "已导出到 diag.zip"
    assert not prop(about, "diagnosticsFailed")

    files = read_archive(target)
    assert sorted(files) == ["about.json", "host.json", "mihomo.log", "settings.json"]
    info = json.loads(files["about.json"])
    assert info["version"] == "0.1.0"
    assert info["language"] == "zh"
    assert info["exported_at"] == datetime.fromtimestamp(NOW, UTC).isoformat(timespec="seconds")
    host = json.loads(files["host.json"])
    assert host["info"]["core_version"] == "mihomo v1.19.33"
    assert host["status"]["state"] == "running"
    assert "www.example.com:443" in files["mihomo.log"]
    assert "WARN  dial failed" in files["mihomo.log"]

    settings = json.loads(files["settings.json"])
    assert settings["settings"]["mixed_port"] == 7890
    assert [p["kind"] for p in settings["profiles"]] == ["subscription", "subscription", "file"]
    assert [p["has_override"] for p in settings["profiles"]] == [False, True, False]
    assert settings["profiles"][0]["active"]
    assert settings["global_override"] is True
    assert settings["routing"]["plans"] >= 1

    everything = "\n".join(files.values())
    for secret in (
        "sub.example.com",
        "token=sample",
        "nodes.example.org",
        "示例订阅",
        "本地配置",
        "config.yaml",
        str(tmp_path),
        "override-secret",
        SECRET_RULE,
        "我的私人方案",
    ):
        assert secret not in everything, secret
    assert not (tmp_path / "out" / "diag.zip.part").exists()
    built.shutdown()


def test_export_without_the_service(qtbot: QtBot, built: ViewModels, tmp_path: Path) -> None:
    class Down(SimulatedHost):
        async def get_info(self) -> HostInfo:
            raise HostError("service_unavailable", "no pipe")

        async def get_status(self) -> HostStatus:
            raise HostError("service_unavailable", "no pipe")

    about = about_for(built, offline_updater(tmp_path))
    about._host = Down()
    target = tmp_path / "diag.zip"
    about.exportDiagnostics(str(target))
    qtbot.waitUntil(lambda: not prop(about, "exporting"), timeout=5000)
    host = json.loads(read_archive(target)["host.json"])
    assert host == {
        "info": {"error": "service_unavailable", "message": "no pipe"},
        "status": {"error": "service_unavailable", "message": "no pipe"},
    }


def test_export_to_a_folder_that_is_not_there(
    qtbot: QtBot, built: ViewModels, tmp_path: Path
) -> None:
    about = about_for(built, offline_updater(tmp_path))
    about.exportDiagnostics(str(tmp_path / "missing" / "diag.zip"))
    qtbot.waitUntil(lambda: not prop(about, "exporting"), timeout=5000)
    assert prop(about, "diagnosticsFailed")
    assert prop(about, "diagnosticsStatus").startswith("导出失败：")


# -- licenses ----------------------------------------------------------------------------------


def test_lists_the_licenses(qtbot: QtBot, built: ViewModels, tmp_path: Path) -> None:
    opened = Opened()
    about = about_for(built, offline_updater(tmp_path), opened)
    model = prop(about, "components")
    about.setComponentQuery("")
    count = model.rowCount()
    assert count > 100
    assert prop(about, "componentsSummary") == f"{count} 个第三方组件"
    names = [row.name for row in model.rows]
    assert "github.com/metacubex/mihomo" in names
    assert {row.category for row in model.rows} == {"Python 依赖", "Go 模块", "Qt", "字体"}

    about.setComponentQuery("MIHOMO")
    assert [row.name for row in model.rows] == ["github.com/metacubex/mihomo"]
    about.setComponentQuery("ofl-1.1")
    assert {row.name for row in model.rows} == {"Inter", "JetBrains Mono"}
    about.setComponentQuery("nothing like this")
    assert prop(about, "componentsEmpty")

    about.openNotices()
    assert [p.name for p in opened.paths] == ["THIRD_PARTY_NOTICES.txt"]


def test_no_manifest(qtbot: QtBot, built: ViewModels, tmp_path: Path) -> None:
    about = about_for(
        built,
        offline_updater(tmp_path),
        manifest=tmp_path / "none.json",
        notices=tmp_path / "none.txt",
    )
    about.setComponentQuery("")
    assert prop(about, "componentsEmpty")
    assert prop(about, "componentsSummary") == "0 个第三方组件"
