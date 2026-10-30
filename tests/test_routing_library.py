"""The routing mode and the user's plans in the library (docs/routing.md §5): which mode the
first import picks, what the tunnel runs in each mode, and that every change is validated,
stored, then reloaded."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from solaris.host.base import HostError, TunnelConfig
from solaris.models import plans
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import ProfileError
from solaris.models.routing import (
    DEFAULT_INFO_PATTERN,
    MODE_PLAN,
    MODE_PROFILE,
    PlanRule,
    RoutingStore,
)
from solaris.models.settings import Settings
from solaris.models.store import ProfileStore
from tests.support.simulated import SAMPLE_CONFIG, SimulatedFetcher, SimulatedHost, seed_profiles

NOW = 1_800_000_000.0
# A subscription with nodes and nothing else, as many airports serve.
NODES_ONLY = "proxies:\n" + "".join(
    f"  - {{name: {name}, type: ss, cipher: aes-128-gcm, password: x, server: {host}, port: 1}}\n"
    for name, host in (
        ("🇭🇰 香港 01", "a.example.net"),
        ("🇯🇵 日本 01", "b.example.net"),
        ("剩余流量 12 GB", "c.example.net"),
    )
)


class Scene:
    def __init__(self, directory: Path, *, seeded: bool = False) -> None:
        self.store = ProfileStore(directory)
        if seeded:
            seed_profiles(self.store, NOW)
        self.host = SimulatedHost(clock=lambda: NOW)
        self.fetcher = SimulatedFetcher()
        self.settings = Settings()
        self.library = ProfileLibrary(
            self.store,
            self.host,
            self.fetcher,
            clock=lambda: NOW,
            settings=lambda: self.settings,
            locale=lambda: "zh",
        )
        self.validated: list[TunnelConfig] = []
        validate = self.host.validate

        async def record(config: TunnelConfig) -> Any:
            self.validated.append(config)
            return await validate(config)

        self.host.validate = record  # type: ignore[method-assign]

    def state(self) -> Any:
        return RoutingStore(self.store.database).state()

    def running_plan(self) -> dict[str, Any]:
        config = self.host.config
        assert config is not None
        assert config.composition is not None
        return json.loads(config.composition.plan_json)


def run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(scenario())


def test_a_first_import_with_nodes_only_routes_with_the_default_plan(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    scene.fetcher.body = NODES_ONLY.encode()

    async def scenario() -> None:
        await scene.host.stop()
        profiles = await scene.library.import_url("https://nodes.example.com/s", "机场")
        # Nodes only: no profile's own rules to use.
        assert profiles.active is None
        assert profiles.profiles[0].proxy_count == 3
        await scene.library.start_tunnel()

    run(scenario)
    state = scene.state()
    assert state.mode == MODE_PLAN
    (plan,) = RoutingStore(scene.store.database).plans()
    assert (state.plan_id, plan.name) == (plan.id, "日常")
    assert [r.content["service"] for r in plan.rules] == [
        "ads",
        "private",
        "openai",
        "anthropic",
        "gemini",
        "cn",
    ]
    config = scene.host.config
    assert config is not None
    assert config.composition is not None
    assert config.config_yaml == ""
    (source,) = config.composition.sources
    assert (source.name, source.yaml) == ("机场", NODES_ONLY)
    assert config.composition.info_filter == DEFAULT_INFO_PATTERN
    assert scene.running_plan()["locale"] == "zh"
    # The information node stays out of the pool; the US rules have no nodes for now.
    routing = run(scene.library.routing)
    assert routing.report is not None
    assert [node.original_name for node in routing.report.nodes] == ["🇭🇰 香港 01", "🇯🇵 日本 01"]
    assert {w.target for w in routing.report.warnings} == {"region:US"}


def test_a_first_import_with_rules_routes_with_them(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await scene.host.stop()
        profiles = await scene.library.import_url("https://sub.example.com/s", "A")
        assert profiles.active == profiles.profiles[0].id
        await scene.library.start_tunnel()
        # A later import does not change the mode.
        scene.fetcher.body = NODES_ONLY.encode()
        await scene.library.import_url("https://nodes.example.com/s", "B")

    run(scenario)
    assert scene.state().mode == MODE_PROFILE
    assert RoutingStore(scene.store.database).plans() == ()
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == SAMPLE_CONFIG
    assert scene.host.reloads == 0  # the new subscription only joins the pool


def test_switching_modes_reloads_what_each_runs(tmp_path: Path) -> None:
    """A plan's composition takes the global override only; a profile's rules take its own
    as well (routing.md §5.6)."""
    scene = Scene(tmp_path, seeded=True)
    scene.store.write_override(None, "log-level: debug\n")
    scene.store.write_override("sample-a", "mode: global\n")

    async def scenario() -> None:
        routing = await scene.library.set_mode(MODE_PLAN)
        assert routing.mode == MODE_PLAN
        assert routing.report is not None
        config = scene.host.config
        assert config is not None
        assert config.composition is not None
        assert config.overrides == ("log-level: debug\n",)
        assert [s.id for s in config.composition.sources] == ["sample-a", "sample-b", "sample-c"]

        routing = await scene.library.set_mode(MODE_PROFILE)
        assert routing.mode == MODE_PROFILE
        config = scene.host.config
        assert config is not None
        assert config.composition is None
        assert config.overrides == ("log-level: debug\n", "mode: global\n")

    run(scenario)
    assert scene.host.reloads == 2
    assert len(RoutingStore(scene.store.database).plans()) == 1


def test_a_plan_change_is_validated_stored_and_reloaded(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)
    run(lambda: scene.library.set_mode(MODE_PLAN))
    store = RoutingStore(scene.store.database)
    (plan,) = store.plans()
    telegram = PlanRule("tg", "service", {"service": "telegram"}, "region:JP")
    changed = plan.__class__(
        plan.id, plan.name, plan.fallback, plan.groups, (telegram, *plan.rules)
    )

    routing = run(lambda: scene.library.put_plan(changed))
    assert store.plan(plan.id).rules[0] == telegram
    assert scene.host.reloads == 2
    assert scene.running_plan()["rules"][0]["items"] == [
        {"type": "GEOSITE", "value": "telegram"},
        {"type": "GEOIP", "value": "telegram", "no_resolve": True},
    ]
    assert routing.report is not None
    assert any(rule.entry_id == "tg" for rule in routing.report.rules)

    # A plan the core refuses is not stored, and the tunnel keeps the one it runs.
    async def refuse(config: TunnelConfig) -> None:
        raise HostError("invalid_config", "rules[0]: unknown category")

    scene.host.validate = refuse  # type: ignore[method-assign]
    with pytest.raises(HostError):
        run(lambda: scene.library.put_plan(plan))
    assert store.plan(plan.id).rules[0] == telegram
    assert scene.host.reloads == 2


def test_plans_are_created_selected_and_deleted(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)

    async def scenario() -> None:
        routing = await scene.library.set_mode(MODE_PLAN)
        first = routing.plan()
        assert first is not None
        with pytest.raises(ProfileError, match="last_plan"):
            await scene.library.delete_plan(first.id)

        routing = await scene.library.create_plan("游戏", first.id)
        second = routing.plan()
        assert second is not None
        assert (second.name, len(second.rules)) == ("游戏", len(first.rules))
        assert routing.state.plan_id == second.id
        blank = await scene.library.create_plan("空白", None)
        assert [p.name for p in blank.plans] == ["日常", "游戏", "空白"]

        routing = await scene.library.select_plan(first.id)
        assert routing.state.plan_id == first.id
        # Deleting the plan in use hands over to the next one.
        routing = await scene.library.delete_plan(first.id)
        assert [p.name for p in routing.plans] == ["游戏", "空白"]
        assert routing.state.plan_id == second.id

    run(scenario)
    assert scene.host.reloads == 5  # the mode, two creations, a selection and the handover


def test_removing_the_last_profile_with_rules_switches_to_the_plan(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)
    for profile_id in ("sample-b", "sample-c"):
        scene.store.write_config(profile_id, NODES_ONLY)

    async def scenario() -> None:
        removal = await scene.library.removal("sample-a")
        assert (removal.active, removal.successor) == (True, None)
        profiles = await scene.library.remove("sample-a")
        assert profiles.active is None

    run(scenario)
    assert scene.state().mode == MODE_PLAN
    config = scene.host.config
    assert config is not None
    assert config.composition is not None
    assert [s.id for s in config.composition.sources] == ["sample-b", "sample-c"]


def test_the_pool_follows_the_profiles(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)

    async def scenario() -> None:
        await scene.library.set_mode(MODE_PLAN)
        removal = await scene.library.removal("sample-c")
        assert removal.pool_nodes == 9
        profiles = await scene.library.set_in_pool("sample-b", False)
        assert not profiles.get("sample-b").in_pool

    run(scenario)
    config = scene.host.config
    assert config is not None
    assert config.composition is not None
    assert [s.id for s in config.composition.sources] == ["sample-a", "sample-c"]
    assert scene.host.reloads == 2
    assert not scene.store.load().get("sample-b").in_pool


def test_a_profiles_own_override_does_not_apply_to_the_plan(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)

    async def scenario() -> None:
        await scene.library.set_mode(MODE_PLAN)
        await scene.library.save_override("sample-a", "mode: direct\n")
        assert scene.host.reloads == 1  # the plan runs; the override waits for its profile
        await scene.library.save_override(None, "mode: global\n")

    run(scenario)
    assert scene.host.reloads == 2
    assert scene.host.config is not None
    assert scene.host.config.overrides == ("mode: global\n",)


def test_the_info_pattern_is_checked_and_stored(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)

    async def scenario() -> None:
        await scene.library.set_mode(MODE_PLAN)
        routing = await scene.library.set_info_pattern("香港")
        assert routing.info_pattern == "香港"
        assert routing.report is not None
        assert all("香港" not in node.name for node in routing.report.nodes)
        assert any(node.info for node in routing.inspections["sample-a"].nodes)
        routing = await scene.library.set_info_pattern(None)
        assert routing.info_pattern == DEFAULT_INFO_PATTERN

    run(scenario)
    assert scene.state().info_pattern is None
    assert scene.host.config is not None
    assert scene.host.config.composition is not None
    assert scene.host.config.composition.info_filter == DEFAULT_INFO_PATTERN


def test_using_a_profiles_rules_needs_some(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)
    scene.store.write_config("sample-b", NODES_ONLY)
    with pytest.raises(ProfileError, match="no_rules"):
        run(lambda: scene.library.activate("sample-b"))
    routing = run(scene.library.inspect_all)
    assert routing.has_rules("sample-a")
    assert routing.has_rules("sample-b") is False


def test_the_plan_uses_the_ui_locale(tmp_path: Path) -> None:
    scene = Scene(tmp_path, seeded=True)
    scene.library = ProfileLibrary(
        scene.store, scene.host, scene.fetcher, clock=lambda: NOW, locale=lambda: "en"
    )
    run(lambda: scene.library.set_mode(MODE_PLAN))
    assert scene.running_plan()["locale"] == "en"
    assert plans.FALLBACK_ENTRY == "fallback"
