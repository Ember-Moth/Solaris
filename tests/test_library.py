import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from solaris.host.base import (
    TAKEOVER_NONE,
    TAKEOVER_SETTINGS,
    TAKEOVER_TUN,
    CompositionReport,
    HostError,
    HostStatus,
    RunOptions,
    TunnelConfig,
)
from solaris.models.library import RETRY_AFTER_S, ProfileLibrary
from solaris.models.profiles import KIND_FILE, ProfileError, Usage
from solaris.models.settings import Settings
from solaris.models.store import ProfileStore
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
    def __init__(
        self,
        directory: Path,
        *,
        seeded: bool = True,
        development: bool = False,
        takeover: str = TAKEOVER_SETTINGS,
    ) -> None:
        self.now = NOW
        self.settings = Settings()
        self.store = ProfileStore(directory)
        if seeded:
            seed_profiles(self.store, NOW)
        self.host = SimulatedHost(
            clock=lambda: self.now, development=development, takeover=takeover
        )
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

    profiles = run(
        lambda: scene.library.profiles.import_url(" https://sub.example.com/x ", "", True)
    )

    (profile,) = profiles.profiles
    assert profiles.active == profile.id  # the first profile is put in use
    assert (profile.name, profile.url) == ("机场", "https://sub.example.com/x")
    assert profile.auto_update_hours == 12
    assert profile.usage == SAMPLE_USAGE
    assert profile.proxy_count == len(NODES)
    assert profile.updated_at == NOW
    assert scene.store.read_config(profile.id) == SAMPLE_CONFIG
    assert scene.store.load() == profiles


NODE_LINK = "vless://12345678-1234-1234-1234-123456789abc@node.example:443?security=tls#Tokyo"


def test_custom_update_interval_is_persisted_and_controls_when_updates_are_due(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, seeded=False)
    imported = run(
        lambda: scene.library.profiles.import_url("https://sub.example/a", interval_hours=6)
    )
    (profile,) = imported.profiles
    assert scene.store.load().get(profile.id).auto_update_hours == 6
    scene.now += 6 * 3600 - 1
    run(scene.library.profiles.update_due)
    assert scene.library.snapshot().get(profile.id).updated_at == NOW
    scene.now += 1
    run(scene.library.profiles.update_due)
    assert scene.library.snapshot().get(profile.id).updated_at == scene.now

    # Editing unrelated metadata and an idempotent enable keep the chosen interval.
    run(lambda: scene.library.profiles.edit(profile.id, "Renamed", profile.url, True))
    run(lambda: scene.library.profiles.set_auto_update(profile.id, True))
    assert scene.store.load().get(profile.id).auto_update_hours == 6
    run(
        lambda: scene.library.profiles.edit(
            profile.id, "Renamed", profile.url, True, interval_hours=24
        )
    )
    last = scene.library.snapshot().get(profile.id).updated_at
    scene.now += 24 * 3600 - 1
    run(scene.library.profiles.update_due)
    assert scene.library.snapshot().get(profile.id).updated_at == last
    scene.now += 1
    run(scene.library.profiles.update_due)
    assert scene.library.snapshot().get(profile.id).updated_at == scene.now

    run(
        lambda: scene.library.profiles.edit(
            profile.id, "Renamed", profile.url, False, interval_hours=0
        )
    )
    last = scene.library.snapshot().get(profile.id).updated_at
    scene.now += 100 * 24 * 3600
    run(scene.library.profiles.update_due)
    assert scene.library.snapshot().get(profile.id).updated_at == last
    assert scene.store.load().get(profile.id).auto_update_hours == 0


@pytest.mark.parametrize("hours", [0, -1, 1.5, True, 2_147_483_648])
def test_invalid_update_interval_neither_downloads_nor_changes_metadata(
    tmp_path: Path, hours
) -> None:
    scene = Scene(tmp_path)
    before = scene.store.load()
    scene.fetcher.error = ProfileError(
        "unexpected_download", "invalid interval reached the fetcher"
    )
    with pytest.raises(ProfileError, match="invalid_update_interval"):
        run(
            lambda: scene.library.profiles.import_url(
                "https://sub.example/new", interval_hours=hours
            )
        )
    with pytest.raises(ProfileError, match="invalid_update_interval"):
        run(
            lambda: scene.library.profiles.edit(
                "sample-a", "New", "https://new.example", True, interval_hours=hours
            )
        )
    assert scene.store.load() == before


def test_custom_update_interval_survives_backup(tmp_path: Path) -> None:
    from solaris.models.backup import REPLACE, Backups

    scene = Scene(tmp_path / "source", seeded=False)
    imported = run(
        lambda: scene.library.profiles.import_url("https://sub.example/a", interval_hours=3)
    )
    snapshot = Backups(scene.store.database, device="source", app_version="1.2.1").snapshot()
    target = ProfileStore(tmp_path / "target")
    try:
        Backups(target.database, device="target", app_version="1.2.1").restore(snapshot, REPLACE)
        assert target.load().get(imported.profiles[0].id).auto_update_hours == 3
    finally:
        target.close()


def test_edit_preserves_profile_content_identity_and_references(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    original = scene.library.snapshot().get("sample-a")
    text = scene.store.read_config(original.id)
    scene.store.write_override(original.id, "prepend-rules: []\n")
    changed = run(
        lambda: scene.library.profiles.edit(
            original.id, "New name", "https://new.example/sub", False
        )
    )
    edited = changed.get(original.id)
    assert edited.id == original.id
    assert (edited.name, edited.url, edited.auto_update_hours) == (
        "New name",
        "https://new.example/sub",
        0,
    )
    assert changed.active == original.id
    assert edited.in_pool == original.in_pool
    assert scene.store.read_config(original.id) == text
    assert scene.store.read_override(original.id) == "prepend-rules: []\n"
    assert edited.usage is None
    assert edited.updated_at == 0
    stored = scene.store.load().get(original.id)
    assert (stored.name, stored.url, stored.auto_update_hours) == (edited.name, edited.url, 0)


@pytest.mark.parametrize(("name", "url"), [(" ", "https://new.example"), ("Name", "file:///bad")])
def test_invalid_profile_edit_keeps_saved_metadata(tmp_path: Path, name: str, url: str) -> None:
    scene = Scene(tmp_path)
    original = scene.store.load()
    with pytest.raises(ProfileError):
        run(lambda: scene.library.profiles.edit("sample-a", name, url, True))
    assert scene.store.load() == original


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_edited_address_rejects_old_inflight_results_even_after_changing_back(
    tmp_path: Path, automatic: bool, fails: bool
) -> None:
    scene = Scene(tmp_path)
    old_url = scene.library.snapshot().get("sample-a").url
    old_text = scene.store.read_config("sample-a")

    async def scenario() -> None:
        started, finish = asyncio.Event(), asyncio.Event()
        fetch = scene.fetcher.fetch

        async def delayed(url: str):
            if url != old_url:
                return await fetch(url)
            started.set()
            await finish.wait()
            if fails:
                raise ProfileError("download_failed", "old address failed")
            return await fetch(url)

        scene.fetcher.body = OTHER_CONFIG.encode()
        scene.fetcher.fetch = delayed
        scene.now += 24 * 3600
        task = asyncio.create_task(
            scene.library.profiles.update_due()
            if automatic
            else scene.library.profiles.update("sample-a")
        )
        await started.wait()
        await scene.library.profiles.edit("sample-a", "New", "https://new.example/sub", False)
        await scene.library.profiles.edit("sample-a", "Final", old_url, False)
        finish.set()
        await task

    run(scenario)
    assert scene.library.snapshot().get("sample-a").name == "Final"
    assert scene.store.read_config("sample-a") == old_text
    assert "sample-a" not in scene.library.snapshot().update_failed


def test_pasted_nodes_join_the_pool_and_survive_backup(tmp_path: Path) -> None:
    from solaris.models.backup import REPLACE, Backups

    scene = Scene(tmp_path / "original", seeded=False)
    text = NODE_LINK + "\n" + "trojan://secret@other.example:443#Singapore"
    profiles = run(lambda: scene.library.profiles.import_links(text, "Shared nodes"))
    (profile,) = profiles.profiles
    assert profile.is_node_links
    assert not profile.can_update
    assert profile.proxy_count == 2
    assert not profile.has_rules
    assert profile.in_pool
    assert profile.auto_update_hours == 0
    assert scene.library.routing_snapshot().mode == "plan"
    assert scene.store.read_config(profile.id) == text + "\n"
    source = Backups(scene.store.database, device="source", app_version="1.2.1")
    target = ProfileStore(tmp_path / "restored")
    Backups(target.database, device="target", app_version="1.2.1").restore(
        source.snapshot(), REPLACE
    )
    assert target.load().get(profile.id).is_node_links
    assert target.load().get(profile.id).unread
    assert target.read_config(profile.id) == text + "\n"
    target.close()
    with pytest.raises(ProfileError, match="no_profile_source"):
        run(lambda: scene.library.profiles.update(profile.id))


def test_node_import_refuses_partial_conversion_without_saving(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    with pytest.raises(ProfileError, match="invalid_node_links"):
        run(lambda: scene.library.profiles.import_links(NODE_LINK + "\nunknown://bad", "Nodes"))
    assert scene.store.load().profiles == ()


def test_node_import_without_core_stores_original_links_for_later_validation(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.host.core_needs_tunnel = True
    run(scene.host.stop)
    profiles = run(
        lambda: scene.library.profiles.import_links(NODE_LINK + "\n" + NODE_LINK, "Nodes")
    )
    (profile,) = profiles.profiles
    assert profile.unread
    assert profile.is_node_links
    assert profile.has_rules is False
    assert scene.store.read_config(profile.id) == NODE_LINK + "\n"
    run(scene.library.tunnel.start)
    reread = scene.library.snapshot().get(profile.id)
    assert not reread.unread
    assert reread.proxy_count == 1


def test_partial_node_import_is_refused_when_an_offline_core_becomes_available(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.host.core_needs_tunnel = True
    run(scene.host.stop)
    profiles = run(
        lambda: scene.library.profiles.import_links(NODE_LINK + "\nunknown://bad", "Nodes")
    )
    with pytest.raises(ProfileError, match="invalid_node_links"):
        run(scene.library.tunnel.start)
    assert scene.library.snapshot().get(profiles.profiles[0].id).unread


def test_restored_unvalidated_node_batch_cannot_silently_lose_bad_links(tmp_path: Path) -> None:
    from solaris.models.backup import REPLACE, Backups

    source = Scene(tmp_path / "source", seeded=False)
    source.host.core_needs_tunnel = True
    run(source.host.stop)
    run(lambda: source.library.profiles.import_links(NODE_LINK + "\nunknown://bad", "Nodes"))
    snapshot = Backups(source.store.database, device="source", app_version="1.2.1").snapshot()
    target = Scene(tmp_path / "target", seeded=False)
    restored = Backups(target.store.database, device="target", app_version="1.2.1")
    run(lambda: target.library.profiles.restore(lambda: restored.restore(snapshot, REPLACE)))
    (profile,) = target.library.snapshot().profiles
    assert profile.unread
    run(target.host.stop)
    with pytest.raises(ProfileError, match="invalid_node_links"):
        run(target.library.tunnel.start)


def test_import_names_the_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    profiles = run(
        lambda: scene.library.profiles.import_url("https://a.example.com/s", "我的订阅", False)
    )
    assert profiles.active == "sample-a"  # importing does not switch
    assert profiles.profiles[-1].name == "我的订阅"
    assert profiles.profiles[-1].auto_update_hours == 0
    profiles = run(lambda: scene.library.profiles.import_url("https://b.example.com/s"))
    assert profiles.profiles[-1].name == "b.example.com"


def test_rejected_config_is_not_stored(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.fetcher.body = b"vmess://not-a-clash-config"
    with pytest.raises(HostError) as raised:
        run(lambda: scene.library.profiles.import_url("https://sub.example.com/x"))
    assert raised.value.code == "invalid_config"
    assert scene.store.load().profiles == ()
    assert not (tmp_path / "profiles").exists()


def test_download_failure_is_reported(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=False)
    scene.fetcher.error = ProfileError("http_error", "HTTP 403")
    with pytest.raises(ProfileError):
        run(lambda: scene.library.profiles.import_url("https://sub.example.com/x"))
    with pytest.raises(ProfileError) as raised:
        run(lambda: scene.library.profiles.import_url("ftp://sub.example.com/x"))
    assert raised.value.code == "invalid_url"
    assert scene.fetcher.requests == ["https://sub.example.com/x"]


def test_import_file(tmp_path: Path) -> None:
    scene = Scene(tmp_path / "data", seeded=False)
    source = tmp_path / "work.yaml"
    source.write_bytes(OTHER_CONFIG.encode())  # Windows would write \r\n

    (profile,) = run(lambda: scene.library.profiles.import_file(source)).profiles

    assert (profile.name, profile.kind, profile.path) == ("work", KIND_FILE, str(source))
    assert profile.proxy_count == 1
    assert scene.store.read_config(profile.id) == OTHER_CONFIG


def test_switching_while_running_reloads_and_keeps_tun(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_config("sample-b", OTHER_CONFIG)

    async def scenario() -> None:
        await scene.host.set_system_proxy(True)
        profiles = await scene.library.profiles.activate("sample-b")
        assert profiles.active == "sample-b"
        status = await scene.host.get_status()
        assert status.state == "running"
        assert (status.tun_enabled, status.system_proxy_enabled) == (True, True)

    run(scenario)
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG
    # The switches stay as the tunnel runs them; the port comes from the settings.
    assert scene.host.config.options == RunOptions(True, True, mixed_port=7890, tun_stack="mips")
    assert scene.store.load().active == "sample-b"


def test_switching_while_stopped_only_stores(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await scene.host.stop()
        await scene.library.profiles.activate("sample-c")

    run(scenario)
    assert scene.host.reloads == 0
    assert scene.store.load().active == "sample-c"


def test_rejected_switch_keeps_the_old_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.store.write_config("sample-b", "just text")
    with pytest.raises(HostError):
        run(lambda: scene.library.profiles.activate("sample-b"))
    assert scene.store.load().active == "sample-a"
    assert run(scene.library.current).active == "sample-a"


def test_updating_the_active_subscription_reloads_in_place(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.fetcher.body = OTHER_CONFIG.encode()
    scene.fetcher.usage = Usage(0, 5, 10, 0)
    scene.now += 60

    update = run(lambda: scene.library.profiles.update("sample-a"))

    assert (update.changed, update.applied) == (True, True)
    profile = update.profiles.get("sample-a")
    assert profile.updated_at == scene.now
    assert profile.usage == Usage(0, 5, 10, 0)
    assert profile.proxy_count == 1
    assert scene.fetcher.requests == [profile.url]
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG
    assert scene.host.config.options.tun_enabled


def test_an_update_with_the_text_stored_already_only_dates_it(tmp_path: Path) -> None:
    """The tunnel runs that text: reloading would only cut new connections short and drop
    the delays measured."""
    scene = Scene(tmp_path)
    scene.fetcher.usage = Usage(0, 5, 10, 0)
    scene.now += 60

    update = run(lambda: scene.library.profiles.update("sample-a"))

    assert (update.changed, update.applied) == (False, False)
    profile = update.profiles.get("sample-a")
    assert (profile.updated_at, profile.usage) == (scene.now, Usage(0, 5, 10, 0))
    assert scene.store.load().get("sample-a") == profile
    assert scene.host.reloads == 0


def test_a_text_the_tunnel_refused_reloads_at_the_next_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload that fails leaves the new text stored, so the next update, by hand or when
    due again after the wait for a failed one, tries again with the same text."""
    scene = Scene(tmp_path)
    scene.fetcher.body = OTHER_CONFIG.encode()
    reload = scene.host.reload

    async def refuse(config: TunnelConfig) -> CompositionReport | None:
        raise HostError("apply_failed", "listen tcp 127.0.0.1:7890: bind: address already in use")

    monkeypatch.setattr(scene.host, "reload", refuse)
    scene.now += 10 * 3600  # A is due
    assert run(scene.library.profiles.update_due).profiles.update_failed == {"sample-a"}
    assert scene.store.read_config("sample-a") == OTHER_CONFIG
    scene.now += RETRY_AFTER_S - 1
    run(scene.library.profiles.update_due)
    assert len(scene.fetcher.requests) == 1  # waits as after a failed download

    monkeypatch.setattr(scene.host, "reload", reload)
    scene.now += 1
    update = run(scene.library.profiles.update_due)
    assert (update.changed, update.applied, update.profiles.update_failed) == (
        True,
        True,
        frozenset(),
    )
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == OTHER_CONFIG


def test_updating_another_profile_does_not_reload(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.profiles.update("sample-b"))
    local = tmp_path / "config.yaml"
    local.write_bytes(OTHER_CONFIG.encode())  # Windows would write \r\n
    update = run(lambda: scene.library.profiles.update("sample-c"))  # re-reads the file
    assert (update.changed, update.applied) == (True, False)
    assert update.profiles.get("sample-c").proxy_count == 1
    assert scene.store.read_config("sample-c") == OTHER_CONFIG
    assert scene.host.reloads == 0


def test_failed_update_is_marked_until_the_next_success(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.fetcher.error = ProfileError("download_failed", "offline")
    with pytest.raises(ProfileError):
        run(lambda: scene.library.profiles.update("sample-b"))
    assert run(scene.library.current).update_failed == {"sample-b"}
    scene.fetcher.error = None
    update = run(lambda: scene.library.profiles.update("sample-b"))
    assert update.profiles.update_failed == frozenset()


def test_automatic_updates_follow_the_interval(tmp_path: Path) -> None:
    # A was updated 2 hours ago every 12 hours; B is updated by hand; C is a file.
    scene = Scene(tmp_path)

    run(scene.library.profiles.update_due)
    assert scene.fetcher.requests == []

    scene.now += 10 * 3600  # A is due now, and in use
    scene.fetcher.body = OTHER_CONFIG.encode()
    update = run(scene.library.profiles.update_due)
    assert scene.fetcher.requests == [scene.library.snapshot().get("sample-a").url]
    assert (update.changed, update.applied) == (True, True)
    assert scene.host.reloads == 1

    run(lambda: scene.library.profiles.set_auto_update("sample-a", False))
    run(lambda: scene.library.profiles.set_auto_update("sample-b", True))
    scene.now += 13 * 3600
    run(scene.library.profiles.update_due)
    assert scene.fetcher.requests[-1] == "https://nodes.example.org/clash.yaml"
    assert len(scene.fetcher.requests) == 2


def test_failed_automatic_update_waits_before_retrying(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    run(lambda: scene.library.profiles.set_auto_update("sample-b", True))  # 3 days old, so due
    scene.fetcher.error = ProfileError("download_failed", "offline")
    assert run(scene.library.profiles.update_due).profiles.update_failed == {"sample-b"}
    run(scene.library.profiles.update_due)
    assert len(scene.fetcher.requests) == 1
    scene.now += RETRY_AFTER_S
    scene.fetcher.error = None
    assert run(scene.library.profiles.update_due).profiles.update_failed == frozenset()
    assert len(scene.fetcher.requests) == 2


def test_removing_profiles(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    profiles = run(lambda: scene.library.profiles.remove("sample-b"))
    assert [p.id for p in profiles.profiles] == ["sample-a", "sample-c"]
    with pytest.raises(ProfileError, match="config_missing"):
        scene.store.read_config("sample-b")

    # The profile in use goes as well: the earliest other one with rules takes over, and the
    # running tunnel reloads with it first (routing.md §5.1).
    profiles = run(lambda: scene.library.profiles.remove("sample-a"))
    assert profiles.active == "sample-c"
    assert scene.host.reloads == 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    assert scene.store.load().active == "sample-c"


def test_removal_states_what_removing_does(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        other = await scene.library.profiles.removal("sample-b")
        assert (other.profile.id, other.active, other.successor) == ("sample-b", False, None)
        active = await scene.library.profiles.removal("sample-a")
        assert active.active
        assert active.successor is not None
        assert active.successor.id == "sample-b"
        await scene.library.profiles.remove("sample-b")
        await scene.library.profiles.remove("sample-c")
        last = await scene.library.profiles.removal("sample-a")
        assert (last.active, last.successor) == (True, None)

    run(scenario)


def test_start_tunnel_uses_the_active_profile(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    settings = Settings(tun=False, mixed_port=7897, allow_lan=True)
    scene.settings = settings

    async def scenario() -> None:
        with pytest.raises(HostError) as raised:
            await scene.library.tunnel.start()
        assert raised.value.code == "already_running"
        await scene.host.stop()
        await scene.library.tunnel.start()

    run(scenario)
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    assert scene.host.config.options == RunOptions(False, True, 7897, True, tun_stack="mips")

    empty = Scene(tmp_path / "empty", seeded=False)
    with pytest.raises(ProfileError) as raised:
        run(lambda: empty.library.tunnel.start())
    assert raised.value.code == "no_profile"


def test_switches_reload_or_wait_for_the_next_start(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await scene.library.tunnel.set_tun(False)
        assert scene.host.reloads == 1
        assert not (await scene.host.get_status()).tun_enabled
        await scene.library.tunnel.set_system_proxy(True)
        assert (await scene.host.get_status()).system_proxy_enabled
        await scene.library.tunnel.apply_settings(Settings(mixed_port=1080, allow_lan=True))
        assert scene.host.config is not None
        assert scene.host.config.options == RunOptions(False, True, 1080, True, tun_stack="mips")

        await scene.host.stop()
        await scene.library.tunnel.set_tun(True)
        await scene.library.tunnel.set_system_proxy(False)
        await scene.library.tunnel.apply_settings(Settings(mixed_port=1081))
        assert scene.host.reloads == 2  # nothing runs, so nothing reloads

    run(scenario)


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        (Settings(system_proxy=False), "tun_unsupported"),
        (Settings(tun=False), "system_proxy_unsupported"),
        (Settings(), "tun_and_system_proxy_unsupported"),
    ],
)
def test_development_service_refuses_the_switches(
    tmp_path: Path, settings: Settings, code: str
) -> None:
    scene = Scene(tmp_path, development=True)
    scene.settings = settings

    async def start() -> None:
        await scene.host.stop()
        await scene.library.tunnel.start()

    with pytest.raises(ProfileError) as raised:
        run(start)
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("takeover", "settings", "runs"),
    [
        # The app's development service takes over nothing: the mixed port serves (D26).
        (TAKEOVER_NONE, Settings(), (False, False)),
        # The macOS extension always runs a TUN, and never sets the system proxy.
        (TAKEOVER_TUN, Settings(tun=False), (True, False)),
        (TAKEOVER_TUN, Settings(), (True, False)),
    ],
)
def test_connecting_takes_over_what_the_host_does(
    tmp_path: Path, takeover: str, settings: Settings, runs: tuple[bool, bool]
) -> None:
    scene = Scene(tmp_path, development=takeover == TAKEOVER_NONE, takeover=takeover)
    scene.settings = settings

    async def scenario() -> HostStatus:
        await scene.host.stop()
        await scene.library.tunnel.start()
        await scene.library.tunnel.apply_settings(replace(settings, mixed_port=1080))
        return await scene.host.get_status()

    status = run(scenario)
    assert (status.tun_enabled, status.system_proxy_enabled) == runs
    assert scene.host.config is not None
    assert scene.host.config.options == RunOptions(*runs, mixed_port=1080, tun_stack="mips")


def test_development_service_refuses_switching_on(tmp_path: Path) -> None:
    scene = Scene(tmp_path, development=True)
    for action, code in (
        (lambda: scene.library.tunnel.set_tun(True), "tun_unsupported"),
        (lambda: scene.library.tunnel.set_system_proxy(True), "system_proxy_unsupported"),
    ):
        with pytest.raises(ProfileError) as raised:
            run(action)
        assert raised.value.code == code
    run(lambda: scene.library.tunnel.set_system_proxy(False))  # switching off is fine

    # Other failures keep their code.
    scene.host.busy_ports.add(1080)
    with pytest.raises(HostError) as failed:
        run(lambda: scene.library.tunnel.apply_settings(Settings(mixed_port=1080)))
    assert failed.value.code == "apply_failed"
