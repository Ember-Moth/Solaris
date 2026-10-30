import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from mihomo_desktop.host.base import HostError, RunOptions
from mihomo_desktop.models.library import RETRY_AFTER_S, ProfileLibrary
from mihomo_desktop.models.profiles import KIND_FILE, ProfileError, Usage
from mihomo_desktop.models.settings import Settings
from mihomo_desktop.models.store import ProfileStore
from tests.support.simulated import (
    NODES,
    SAMPLE_CONFIG,
    SAMPLE_USAGE,
    SimulatedFetcher,
    SimulatedHost,
    seed_profiles,
)

NOW = 1_800_000_000.0
OTHER_CONFIG = (
    "proxies:\n  - {name: 东京, type: ss, server: a.example.net, port: 1}\n"
    "rules:\n  - MATCH,DIRECT\n"
)


class Scene:
    def __init__(self, directory: Path, *, seeded: bool = True, development: bool = False) -> None:
        self.now = NOW
        self.settings = Settings()
        self.store = ProfileStore(directory)
        if seeded:
            seed_profiles(self.store, NOW)
        self.host = SimulatedHost(clock=lambda: self.now, development=development)
        self.fetcher = SimulatedFetcher()
        self.library = ProfileLibrary(
            self.store,
            self.host,
            self.fetcher,
            clock=lambda: self.now,
            settings=lambda: self.settings,
        )


def run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(scenario())


def test_import_url_validates_and_stores(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.fetcher.filename = "机场"

    profiles = run(lambda: scene.library.import_url(" https://sub.example.com/x ", "", True))

    (profile,) = profiles.profiles
    assert profiles.active == profile.id  # the first profile is put in use
    assert (profile.name, profile.url) == ("机场", "https://sub.example.com/x")
    assert profile.auto_update_hours == 12
    assert profile.usage == SAMPLE_USAGE
    assert profile.proxy_count == len(NODES)
    assert profile.updated_at == NOW
    assert scene.store.read_config(profile.id) == SAMPLE_CONFIG
    assert scene.store.load() == profiles


def test_import_names_the_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    profiles = run(lambda: scene.library.import_url("https://a.example.com/s", "我的订阅", False))
    assert profiles.active == "sample-a"  # importing does not switch
    assert profiles.profiles[-1].name == "我的订阅"
    assert profiles.profiles[-1].auto_update_hours == 0
    profiles = run(lambda: scene.library.import_url("https://b.example.com/s"))
    assert profiles.profiles[-1].name == "b.example.com"


def test_rejected_config_is_not_stored(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.fetcher.body = b"vmess://not-a-clash-config"
    with pytest.raises(HostError) as raised:
        run(lambda: scene.library.import_url("https://sub.example.com/x"))
    assert raised.value.code == "invalid_config"
    assert scene.store.load().profiles == ()
    assert not (tmp_path / "profiles").exists()


def test_download_failure_is_reported(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.fetcher.error = ProfileError("http_error", "HTTP 403")
    with pytest.raises(ProfileError):
        run(lambda: scene.library.import_url("https://sub.example.com/x"))
    with pytest.raises(ProfileError) as raised:
        run(lambda: scene.library.import_url("ftp://sub.example.com/x"))
    assert raised.value.code == "invalid_url"
    assert scene.fetcher.requests == ["https://sub.example.com/x"]


def test_import_file(tmp_path: Path) -> None:
    scene = Scene(tmp_path / "data", seeded=False)
    source = tmp_path / "work.yaml"
    source.write_text(OTHER_CONFIG, encoding="utf-8")

    (profile,) = run(lambda: scene.library.import_file(source)).profiles

    assert (profile.name, profile.kind, profile.path) == ("work", KIND_FILE, str(source))
    assert profile.proxy_count == 1
    assert scene.store.read_config(profile.id) == OTHER_CONFIG


def test_switching_while_running_reloads_and_keeps_tun(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_config("sample-b", OTHER_CONFIG)

    async def scenario() -> None:
        await scene.host.set_system_proxy(True)
        profiles = await scene.library.activate("sample-b")
        assert profiles.active == "sample-b"
        status = await scene.host.get_status()
        assert status.state == "running"
        assert (status.tun_enabled, status.system_proxy_enabled) == (True, True)

    run(scenario)
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG
    # The switches stay as the tunnel runs them; the port comes from the settings.
    assert scene.host.config.options == RunOptions(True, True, mixed_port=7890)
    assert scene.store.load().active == "sample-b"


def test_switching_while_stopped_only_stores(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await scene.host.stop()
        await scene.library.activate("sample-c")

    run(scenario)
    assert scene.host.reloads == 0
    assert scene.store.load().active == "sample-c"


def test_rejected_switch_keeps_the_old_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_config("sample-b", "just text")
    with pytest.raises(HostError):
        run(lambda: scene.library.activate("sample-b"))
    assert scene.store.load().active == "sample-a"
    assert run(scene.library.current).active == "sample-a"


def test_updating_the_active_subscription_reloads_in_place(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.fetcher.body = OTHER_CONFIG.encode()
    scene.fetcher.usage = Usage(0, 5, 10, 0)
    scene.now += 60

    profiles = run(lambda: scene.library.update("sample-a"))

    profile = profiles.get("sample-a")
    assert profile.updated_at == scene.now
    assert profile.usage == Usage(0, 5, 10, 0)
    assert profile.proxy_count == 1
    assert scene.fetcher.requests == [profile.url]
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG
    assert scene.host.config.options.tun_enabled


def test_updating_another_profile_does_not_reload(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.update("sample-b"))
    local = tmp_path / "config.yaml"
    local.write_text(OTHER_CONFIG, encoding="utf-8")
    profiles = run(lambda: scene.library.update("sample-c"))  # re-reads the file
    assert profiles.get("sample-c").proxy_count == 1
    assert scene.store.read_config("sample-c") == OTHER_CONFIG
    assert scene.host.reloads == 0


def test_failed_update_is_marked_until_the_next_success(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.fetcher.error = ProfileError("download_failed", "offline")
    with pytest.raises(ProfileError):
        run(lambda: scene.library.update("sample-b"))
    assert run(scene.library.current).update_failed == {"sample-b"}
    scene.fetcher.error = None
    assert run(lambda: scene.library.update("sample-b")).update_failed == frozenset()


def test_automatic_updates_follow_the_interval(tmp_path: Path) -> None:
    # A was updated 2 hours ago every 12 hours; B is updated by hand; C is a file.
    scene = Scene(tmp_path)

    run(scene.library.update_due)
    assert scene.fetcher.requests == []

    scene.now += 10 * 3600  # A is due now, and in use
    run(scene.library.update_due)
    assert scene.fetcher.requests == [scene.library.snapshot().get("sample-a").url]
    assert scene.host.reloads == 1

    run(lambda: scene.library.set_auto_update("sample-a", False))
    run(lambda: scene.library.set_auto_update("sample-b", True))
    scene.now += 13 * 3600
    run(scene.library.update_due)
    assert scene.fetcher.requests[-1] == "https://nodes.example.org/clash.yaml"
    assert len(scene.fetcher.requests) == 2


def test_failed_automatic_update_waits_before_retrying(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.set_auto_update("sample-b", True))  # 3 days old, so due
    scene.fetcher.error = ProfileError("download_failed", "offline")
    assert run(scene.library.update_due).update_failed == {"sample-b"}
    run(scene.library.update_due)
    assert len(scene.fetcher.requests) == 1
    scene.now += RETRY_AFTER_S
    scene.fetcher.error = None
    assert run(scene.library.update_due).update_failed == frozenset()
    assert len(scene.fetcher.requests) == 2


def test_removing_profiles(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    profiles = run(lambda: scene.library.remove("sample-b"))
    assert [p.id for p in profiles.profiles] == ["sample-a", "sample-c"]
    with pytest.raises(ProfileError, match="config_missing"):
        scene.store.read_config("sample-b")

    # The profile in use goes as well: the earliest other one with rules takes over, and the
    # running tunnel reloads with it first (routing.md §5.1).
    profiles = run(lambda: scene.library.remove("sample-a"))
    assert profiles.active == "sample-c"
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    assert scene.store.load().active == "sample-c"


def test_removal_states_what_removing_does(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        other = await scene.library.removal("sample-b")
        assert (other.profile.id, other.active, other.successor) == ("sample-b", False, None)
        active = await scene.library.removal("sample-a")
        assert active.active
        assert active.successor is not None
        assert active.successor.id == "sample-b"
        await scene.library.remove("sample-b")
        await scene.library.remove("sample-c")
        last = await scene.library.removal("sample-a")
        assert (last.active, last.successor) == (True, None)

    run(scenario)


def test_start_tunnel_uses_the_active_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    settings = Settings(system_proxy=True, mixed_port=7897, allow_lan=True)
    scene.settings = settings

    async def scenario() -> None:
        with pytest.raises(HostError) as raised:
            await scene.library.start_tunnel()
        assert raised.value.code == "already_running"
        await scene.host.stop()
        await scene.library.start_tunnel()

    run(scenario)
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    assert scene.host.config.options == RunOptions(False, True, 7897, True)

    empty = Scene(tmp_path / "empty", seeded=False)
    with pytest.raises(ProfileError) as raised:
        run(lambda: empty.library.start_tunnel())
    assert raised.value.code == "no_profile"


def test_switches_reload_or_wait_for_the_next_start(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await scene.library.set_tun(False)
        assert scene.host.reloads == 1
        assert not (await scene.host.get_status()).tun_enabled
        await scene.library.set_system_proxy(True)
        assert (await scene.host.get_status()).system_proxy_enabled
        await scene.library.apply_settings(Settings(mixed_port=1080, allow_lan=True))
        assert scene.host.config is not None
        assert scene.host.config.options == RunOptions(False, True, 1080, True)

        await scene.host.stop()
        await scene.library.set_tun(True)
        await scene.library.set_system_proxy(False)
        await scene.library.apply_settings(Settings(mixed_port=1081))
        assert scene.host.reloads == 2  # nothing runs, so nothing reloads

    run(scenario)


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        (Settings(tun=True), "tun_unsupported"),
        (Settings(system_proxy=True), "system_proxy_unsupported"),
        (Settings(tun=True, system_proxy=True), "tun_and_system_proxy_unsupported"),
    ],
)
def test_development_service_refuses_the_switches(
    tmp_path: Path, settings: Settings, code: str
) -> None:
    scene = Scene(tmp_path, development=True)
    scene.settings = settings

    async def start() -> None:
        await scene.host.stop()
        await scene.library.start_tunnel()

    with pytest.raises(ProfileError) as raised:
        run(start)
    assert raised.value.code == code


def test_development_service_refuses_switching_on(tmp_path: Path) -> None:
    scene = Scene(tmp_path, development=True)
    for action, code in (
        (lambda: scene.library.set_tun(True), "tun_unsupported"),
        (lambda: scene.library.set_system_proxy(True), "system_proxy_unsupported"),
    ):
        with pytest.raises(ProfileError) as raised:
            run(action)
        assert raised.value.code == code
    run(lambda: scene.library.set_system_proxy(False))  # switching off is fine

    # Other failures keep their code.
    scene.host.busy_ports.add(1080)
    with pytest.raises(HostError) as failed:
        run(lambda: scene.library.apply_settings(Settings(mixed_port=1080)))
    assert failed.value.code == "apply_failed"
