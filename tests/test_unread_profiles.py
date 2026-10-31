"""Profiles stored while no core can read them (D27): on macOS the core runs only while the
tunnel is connected. They are read when it connects, before Start; until then nothing is
validated, and the routing page leaves out what needs a core."""

from dataclasses import replace
from pathlib import Path

import pytest

from solaris.host.base import HostError
from solaris.models.profiles import ProfileError
from solaris.models.routing import MODE_PLAN, MODE_PROFILE
from tests.support.simulated import SAMPLE_CONFIG
from tests.test_routing_library import NODES_ONLY, Scene, run

BROKEN_RULES = "proxies: []\nrules:\n  - MATCH,nowhere\n"


def mac_scene(directory: Path) -> Scene:
    """A scene whose core, like the macOS extension's, answers only while the tunnel runs."""
    scene = Scene(directory)
    scene.host.core_needs_tunnel = True
    run(scene.host.stop)
    return scene


def test_an_import_without_a_core_is_stored_unread_and_read_at_connect(tmp_path: Path) -> None:
    scene = mac_scene(tmp_path)
    scene.fetcher.body = SAMPLE_CONFIG.encode()

    profiles = run(lambda: scene.library.profiles.import_url("https://sub.example.com/a", "A"))
    (profile,) = profiles.profiles
    assert (profile.unread, profile.proxy_count, profiles.active) == (True, None, None)
    assert scene.validated == []
    assert scene.state().mode is None  # the import settles it once the core read it
    assert run(scene.library.routing).has_rules(profile) is None

    run(scene.library.tunnel.start)
    assert scene.host.prepared == 1
    profiles = run(scene.library.current)
    (profile,) = profiles.profiles
    assert not profile.unread
    assert profile.proxy_count == 9
    # What the first import would have settled: its own rules, in use.
    assert (scene.state().mode, profiles.active) == (MODE_PROFILE, profile.id)
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    # Stored: the next start has nothing to read.
    assert not run(Scene(tmp_path).library.current).profiles[0].unread


def test_a_profile_in_use_the_core_refuses_takes_the_tunnel_down(tmp_path: Path) -> None:
    scene = mac_scene(tmp_path)
    scene.fetcher.body = BROKEN_RULES.encode()
    run(lambda: scene.library.profiles.import_url("https://sub.example.com/broken", "Broken"))

    with pytest.raises(HostError) as raised:
        run(scene.library.tunnel.start)
    assert raised.value.code == "invalid_config"
    assert not scene.host.core_available  # down again, and nothing started
    assert scene.host.config is None
    profiles = run(scene.library.current)
    (profile,) = profiles.profiles
    assert profile.unread
    assert profile.id in profiles.update_failed


def test_another_refused_profile_is_marked_and_left_out(tmp_path: Path) -> None:
    scene = mac_scene(tmp_path)
    scene.fetcher.body = NODES_ONLY.encode()
    run(lambda: scene.library.profiles.import_url("https://nodes.example.com/a", "Nodes"))
    scene.fetcher.body = BROKEN_RULES.encode()
    run(lambda: scene.library.profiles.import_url("https://sub.example.com/broken", "Broken"))

    run(scene.library.tunnel.start)
    profiles = run(scene.library.current)
    nodes, broken = profiles.profiles
    assert scene.state().mode == MODE_PLAN  # the first one read has nodes only
    assert not nodes.unread
    assert nodes.proxy_count == 2  # without the info node
    assert broken.unread
    assert broken.id in profiles.update_failed
    config = scene.host.config
    assert config is not None
    assert config.composition is not None
    assert [source.name for source in config.composition.sources] == ["Nodes"]


def test_without_a_core_changes_are_stored_and_start_checks_them(tmp_path: Path) -> None:
    scene = mac_scene(tmp_path)
    scene.fetcher.body = NODES_ONLY.encode()
    run(lambda: scene.library.profiles.import_url("https://nodes.example.com/a", "Nodes"))
    run(scene.library.tunnel.start)
    run(scene.host.stop)
    routing = run(scene.library.routing)
    plan = routing.plan()
    assert plan is not None
    validated = len(scene.validated)

    first = replace(plan.rules[0], enabled=False)
    changed = run(
        lambda: scene.library.plans.put_plan(replace(plan, rules=(first, *plan.rules[1:])))
    )
    assert routing.report is not None
    assert routing.report.nodes
    assert changed.report is None  # the counts wait for a core
    assert len(scene.validated) == validated
    assert run(scene.library.plans.compose_report).report is None
    # Automatic updates wait for a core to read them.
    scene.fetcher.body = SAMPLE_CONFIG.encode()
    profiles = run(scene.library.profiles.update_due)
    assert not profiles.profiles[0].unread

    run(scene.library.tunnel.start)
    assert not scene.running_plan()["rules"][0]["enabled"]
    assert run(scene.library.routing).report is not None


def relaunched_without_a_core(directory: Path) -> Scene:
    """The app started again with the tunnel down: nothing read in memory."""
    return mac_scene(directory)


def test_after_a_restart_a_nodes_only_profile_never_takes_over_the_rules(
    tmp_path: Path,
) -> None:
    connected = Scene(tmp_path)
    connected.fetcher.body = SAMPLE_CONFIG.encode()
    rules = run(lambda: connected.library.profiles.import_url("https://sub.example.com/a", "Rules"))
    connected.fetcher.body = NODES_ONLY.encode()
    run(lambda: connected.library.profiles.import_url("https://nodes.example.com/b", "Nodes"))
    assert connected.state().mode == MODE_PROFILE

    scene = relaunched_without_a_core(tmp_path)
    profiles = run(scene.library.current)
    routing = run(scene.library.routing)
    assert [routing.has_rules(p) for p in profiles.profiles] == [True, False]
    run(lambda: scene.library.profiles.remove(rules.profiles[0].id))
    # Nodes only: the plan takes over, as when the core can answer.
    assert scene.state().mode == MODE_PLAN
    assert run(scene.library.current).active is None
    with pytest.raises(ProfileError, match="no_rules"):
        run(lambda: scene.library.plans.set_mode(MODE_PROFILE))
    run(scene.library.tunnel.start)
    config = scene.host.config
    assert config is not None
    assert config.composition is not None


def test_an_update_the_core_refuses_at_connect_falls_back(tmp_path: Path) -> None:
    connected = Scene(tmp_path)
    connected.fetcher.body = SAMPLE_CONFIG.encode()
    (rules,) = run(
        lambda: connected.library.profiles.import_url("https://sub.example.com/a", "A")
    ).profiles

    scene = relaunched_without_a_core(tmp_path)
    scene.fetcher.body = BROKEN_RULES.encode()
    profiles = run(lambda: scene.library.profiles.update(rules.id))
    assert profiles.profiles[0].unread
    assert profiles.profiles[0].has_rules is None
    scene.fetcher.body = (BROKEN_RULES + "# again\n").encode()
    run(lambda: scene.library.profiles.update(rules.id))  # another one waiting keeps the read text

    run(scene.library.tunnel.start)
    # The text the core read before stays in use, as after a failed update.
    config = scene.host.config
    assert config is not None
    assert config.config_yaml == SAMPLE_CONFIG
    profiles = run(scene.library.current)
    (profile,) = profiles.profiles
    assert not profile.unread
    assert profile.has_rules
    assert profile.id in profiles.update_failed
    assert scene.store.read_config(profile.id) == SAMPLE_CONFIG
