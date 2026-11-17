"""An automatic fallback must not become an explicit preference for the user's plan."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from solaris.host.base import HostError, TunnelConfig
from solaris.models.library import ProfileLibrary
from solaris.models.routing import MODE_PLAN, MODE_PROFILE, Plan, RoutingStore
from tests.support.simulated import SAMPLE_CONFIG
from tests.test_routing_library import NODES_ONLY, Scene


async def fall_back(scene: Scene) -> None:
    imported = await scene.library.profiles.import_url("https://example.invalid/a", "A")
    await scene.library.profiles.remove(imported.profiles[0].id)
    assert scene.state().mode == MODE_PLAN
    assert scene.state().resume_profile


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_reimport_recovers_rules_and_preserves_the_plan(
    tmp_path: Path, running: bool, restart: bool
) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        if not running:
            await scene.host.stop()
        await fall_back(scene)
        plans = RoutingStore(scene.store.database).plans()
        if restart:
            scene.library = ProfileLibrary(scene.store, scene.host, scene.fetcher)
        imported = await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PROFILE
        assert not scene.state().resume_profile
        assert imported.active == imported.profiles[0].id
        assert RoutingStore(scene.store.database).plans() == plans
        if running:
            assert scene.host.config is not None
            assert scene.host.config.config_yaml == SAMPLE_CONFIG
            assert scene.host.config.composition is None

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["mode", "select", "edit", "create", "convert"])
def test_explicit_plan_choice_cancels_recovery(tmp_path: Path, action: str) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await fall_back(scene)
        library = scene.library
        plan = (await library.routing()).plan()
        assert plan is not None
        if action == "mode":
            reloads = scene.host.reloads
            await library.plans.set_mode(MODE_PLAN)
            assert scene.host.reloads == reloads
        elif action == "select":
            await library.plans.select_plan(plan.id)
        elif action == "edit":
            await library.plans.put_plan(replace(plan, name="My rules"))
        elif action == "create":
            await library.plans.create_plan("Chosen", None)
        else:
            await library.plans.add_plan(Plan("imported", "Imported", "DIRECT"), use=True)
        assert not scene.state().resume_profile
        await library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PLAN
        assert scene.host.config is not None
        assert scene.host.config.composition is not None

    asyncio.run(scenario())


def test_an_existing_explicit_plan_is_not_reset_by_deletion(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        p = await scene.library.profiles.import_url("https://example.invalid/a", "A")
        await scene.library.plans.set_mode(MODE_PLAN)
        await scene.library.profiles.remove(p.profiles[0].id)
        assert not scene.state().resume_profile
        await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PLAN

    asyncio.run(scenario())


def test_nodes_and_internal_recomposition_do_not_cancel_recovery(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await fall_back(scene)
        scene.fetcher.body = NODES_ONLY.encode()
        p = await scene.library.profiles.import_url("https://example.invalid/nodes", "Nodes")
        await scene.library.profiles.set_in_pool(p.profiles[0].id, False)
        await scene.library.plans.add_plan(Plan("unused", "Unused", "DIRECT"), use=False)
        await scene.library.plans.put_plan(Plan("unused", "Edited unused", "REJECT"))
        assert scene.state().resume_profile
        scene.fetcher.body = SAMPLE_CONFIG.encode()
        p = await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PROFILE
        assert p.active == p.profiles[-1].id

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["validate", "reload"])
def test_failed_import_keeps_recovery_and_running_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    scene = Scene(tmp_path)

    async def refused(config: TunnelConfig) -> None:
        raise HostError("invalid_config", "rejected by test host")

    async def scenario() -> None:
        await fall_back(scene)
        before = scene.host.config
        with monkeypatch.context() as patch:
            patch.setattr(scene.host, failure, refused)
            with pytest.raises(HostError, match="invalid_config"):
                await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PLAN
        assert scene.state().resume_profile
        assert not scene.library.snapshot().profiles
        assert scene.host.config == before
        await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert scene.state().mode == MODE_PROFILE

    asyncio.run(scenario())


@pytest.mark.parametrize("reject_start", [False, True])
def test_deferred_import_recovers_only_after_a_successful_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reject_start: bool
) -> None:
    scene = Scene(tmp_path)

    async def refused(config: TunnelConfig) -> None:
        raise HostError("apply_failed", "could not open port")

    async def scenario() -> None:
        await fall_back(scene)
        await scene.host.stop()
        scene.host.core_needs_tunnel = True
        p = await scene.library.profiles.import_url("https://example.invalid/b", "B")
        assert p.profiles[0].unread
        scene.library = ProfileLibrary(scene.store, scene.host, scene.fetcher)
        if reject_start:
            with monkeypatch.context() as patch:
                patch.setattr(scene.host, "start", refused)
                with pytest.raises(HostError, match="apply_failed"):
                    await scene.library.tunnel.start()
            assert scene.state().mode == MODE_PLAN
            assert scene.state().resume_profile
            # Inspection already succeeded; recovery must survive this and a restart.
            assert not scene.library.snapshot().profiles[0].unread
            scene.library = ProfileLibrary(scene.store, scene.host, scene.fetcher)
        await scene.library.tunnel.start()
        assert scene.state().mode == MODE_PROFILE
        assert not scene.state().resume_profile
        assert scene.host.config is not None
        assert scene.host.config.composition is None
        assert scene.host.config.config_yaml == SAMPLE_CONFIG

    asyncio.run(scenario())


def test_another_subscription_with_rules_takes_over_without_pending_recovery(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        a = await scene.library.profiles.import_url("https://example.invalid/a", "A")
        b = await scene.library.profiles.import_url("https://example.invalid/b", "B")
        await scene.library.profiles.remove(a.profiles[0].id)
        assert scene.library.snapshot().active == b.profiles[-1].id
        assert scene.state().mode == MODE_PROFILE
        assert not scene.state().resume_profile

    asyncio.run(scenario())


def test_failed_plan_edit_does_not_cancel_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(tmp_path)

    async def refused(config: TunnelConfig) -> None:
        raise HostError("invalid_config", "refused edit")

    async def scenario() -> None:
        await fall_back(scene)
        plan = (await scene.library.routing()).plan()
        assert plan is not None
        with monkeypatch.context() as patch:
            patch.setattr(scene.host, "validate", refused)
            with pytest.raises(HostError, match="invalid_config"):
                await scene.library.plans.put_plan(replace(plan, name="Refused"))
        assert scene.state().resume_profile
        assert RoutingStore(scene.store.database).plan(plan.id) == plan

    asyncio.run(scenario())


def test_invalid_deferred_import_keeps_waiting_for_valid_rules(tmp_path: Path) -> None:
    scene = Scene(tmp_path)

    async def scenario() -> None:
        await fall_back(scene)
        await scene.host.stop()
        scene.host.core_needs_tunnel = True
        scene.fetcher.body = b"rules: [this-is-not-a-rule]\n"
        await scene.library.profiles.import_url("https://example.invalid/bad", "Bad")
        # The invalid new subscription cannot take over; the fallback can still run.
        await scene.library.tunnel.start()
        assert scene.state().mode == MODE_PLAN
        assert scene.state().resume_profile
        assert scene.library.snapshot().profiles[0].unread
        scene.fetcher.body = SAMPLE_CONFIG.encode()
        await scene.library.profiles.import_url("https://example.invalid/good", "Good")
        assert scene.state().mode == MODE_PROFILE
        assert not scene.state().resume_profile

    asyncio.run(scenario())
