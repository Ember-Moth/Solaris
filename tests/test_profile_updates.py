"""Updating a subscription while the tunnel runs (docs/routing.md §5.4): a new text in use
reaches the running core, whether the update was asked for or due, and the pages that show
what the core runs read it again; the text stored already reloads nothing."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from solaris.host.base import ProxyGroup
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.simulated import SAMPLE_CONFIG, SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded, kind

NOW = datetime(2026, 10, 29, 12).timestamp()
# The sample with a rule more, and with a node more.
MORE_RULES = SAMPLE_CONFIG.replace("rules:\n", "rules:\n  - DOMAIN-SUFFIX,example.com,DIRECT\n")
MORE_NODES = SAMPLE_CONFIG.replace(
    "proxy-groups:",
    "  - {name: 香港 04, type: http, server: node9.example.net, port: 443}\nproxy-groups:",
)


def test_profile_editor_keeps_bad_input_and_saves_metadata(qtbot: QtBot, loaded: Loaded) -> None:
    models = loaded.view_models
    models.shell.setPage("profiles")
    original = models.library.snapshot().get("sample-a")
    models.profiles.editProfile(original.id)
    qtbot.waitUntil(lambda: loaded.focused_text() == original.name)
    name = loaded.overlay_items(
        lambda item: kind(item) == "AppTextField" and item.property("text") == original.name
    )[0]
    address = loaded.overlay_items(
        lambda item: kind(item) == "AppTextField" and item.property("text") == original.url
    )[0]
    name.setProperty("text", "Renamed subscription")
    address.setProperty("text", "file:///not-a-subscription")
    save = loaded.overlay_items(
        lambda item: (
            kind(item) == "AppButton" and item.property("text") == "保存" and item.isVisible()
        )
    )[0]
    loaded.click(qtbot, save)
    qtbot.waitUntil(lambda: bool(models.profiles.editError))
    assert models.library.snapshot().get(original.id).name == original.name
    assert address.property("text") == "file:///not-a-subscription"
    address.setProperty("text", "https://updated.example/sub")
    with qtbot.waitSignal(models.profiles.editSaved):
        loaded.click(qtbot, save)
    updated = models.library.snapshot().get(original.id)
    assert (updated.name, updated.url) == ("Renamed subscription", "https://updated.example/sub")
    assert loaded.warnings == []


def test_profile_editor_validates_and_restores_the_selected_interval(
    qtbot: QtBot, loaded: Loaded
) -> None:
    models = loaded.view_models
    models.shell.setPage("profiles")
    models.profiles.editProfile("sample-a")
    qtbot.waitUntil(lambda: loaded.focused_text() == "示例订阅 A")
    field = loaded.overlay_items(lambda item: kind(item) == "AutoUpdateField" and item.isVisible())[
        0
    ]
    save = loaded.overlay_items(
        lambda item: (
            kind(item) == "AppButton" and item.property("text") == "保存" and item.isVisible()
        )
    )[0]
    for invalid in ("", "0", "-1", "1.5", "1,000"):
        field.setProperty("intervalText", invalid)
        assert not save.isEnabled()
    field.setProperty("intervalText", "6")
    with qtbot.waitSignal(models.profiles.editSaved):
        loaded.click(qtbot, save)
    assert models.library.snapshot().get("sample-a").auto_update_hours == 6
    assert rows(models.profiles, "model")[0].autoUpdateLabel == "每 6 小时自动更新"

    models.profiles.editProfile("sample-a")
    qtbot.waitUntil(lambda: loaded.focused_text() == "示例订阅 A")
    assert field.property("intervalText") == "6"
    field.setProperty("checked", False)
    with qtbot.waitSignal(models.profiles.editSaved):
        loaded.click(qtbot, save)
    assert models.library.snapshot().get("sample-a").auto_update_hours == 0
    assert loaded.warnings == []


def test_import_form_saves_a_custom_interval(qtbot: QtBot, loaded: Loaded) -> None:
    models = loaded.view_models
    models.profiles.prefillImport("https://new.example/sub", "Custom interval")
    models.shell.setPage("profiles")
    qtbot.waitUntil(lambda: loaded.focused_text() == "https://new.example/sub")
    field = loaded.item(lambda item: kind(item) == "AutoUpdateField" and item.isVisible())
    field.setProperty("intervalText", "3")
    submit = loaded.item(
        lambda item: (
            kind(item) == "AppButton" and item.property("text") == "导入" and item.isVisible()
        )
    )
    with qtbot.waitSignal(models.profiles.imported):
        loaded.click(qtbot, submit)
    assert models.library.snapshot().profiles[-1].auto_update_hours == 3
    assert loaded.warnings == []


def test_node_link_dialog_imports_a_batch_without_downloading(qtbot: QtBot, loaded: Loaded) -> None:
    models = loaded.view_models
    models.shell.setPage("profiles")
    qtbot.waitUntil(lambda: models.shell.currentPage == "profiles")
    button = loaded.item(
        lambda item: kind(item) == "AppButton" and item.property("text") == "导入节点"
    )
    loaded.click(qtbot, button)
    editors = loaded.overlay_items(lambda item: kind(item) == "CodeEditor" and item.isVisible())
    assert len(editors) == 1
    editors[0].setProperty(
        "text",
        "vless://12345678-1234-1234-1234-123456789abc@node.example:443#Tokyo\ntrojan://secret@other.example:443#Singapore",
    )
    submit = loaded.overlay_items(
        lambda item: (
            kind(item) == "AppButton" and item.property("text") == "导入" and item.isVisible()
        )
    )[0]
    before = len(models.library.snapshot().profiles)
    with qtbot.waitSignal(models.profiles.imported):
        loaded.click(qtbot, submit)
    profiles = models.library.snapshot().profiles
    assert len(profiles) == before + 1
    assert profiles[-1].is_node_links
    assert profiles[-1].proxy_count == 2
    assert loaded.warnings == []


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def rows(view_model: QObject, name: str) -> list[Any]:
    return list(prop(view_model, name).rows)


class Scene:
    def __init__(self, directory: Path) -> None:
        self.now = NOW
        store = ProfileStore(directory)
        seed_profiles(store, NOW)
        # Downloads take a moment, so nothing can be done before the call returns.
        self.fetcher = SimulatedFetcher(delay_s=0.1)
        self.host = SimulatedHost()
        self.view_models: ViewModels = compose(self.host, store, self.fetcher, lambda: self.now)
        self.groups_read = 0  # list_groups calls: the proxies page and the overview
        list_groups = self.host.list_groups

        async def counted() -> list[ProxyGroup]:
            self.groups_read += 1
            return await list_groups()

        self.host.list_groups = counted  # type: ignore[method-assign]

    def rules(self) -> list[tuple[str, str]]:
        """The rules the routing page lists for 订阅自带."""
        return [
            (r.match, r.target) for r in rows(self.view_models.routing._profile_routes, "rules")
        ]

    def rule_notes(self) -> dict[str, str]:
        options = prop(self.view_models.routing._profile_routes, "options")
        return {option["label"]: option["note"] for option in options}


@pytest.fixture
def scene(qtbot: QtBot, tmp_path: Path) -> Iterator[Scene]:
    built = Scene(tmp_path)
    built.view_models.start()
    qtbot.waitUntil(lambda: prop(built.view_models.overview, "connected"))
    yield built
    built.view_models.shutdown()


def show_profile_rules(qtbot: QtBot, scene: Scene) -> None:
    """Show the routing page, which lists the rules of 示例订阅 A as the core runs them."""
    scene.view_models.shell.setPage("routing")
    qtbot.waitUntil(lambda: prop(scene.view_models.routing._profile_routes, "running"))
    assert scene.rules() == [("MATCH", "节点选择")]


def use_plan(qtbot: QtBot, scene: Scene) -> None:
    routing, profiles = scene.view_models.routing, scene.view_models.profiles
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: prop(routing, "summary") == "使用「日常」 · 节点池 27 个节点")
    qtbot.waitUntil(lambda: prop(profiles, "summary") == "3 份订阅 · 节点池 27 个节点")


@pytest.mark.parametrize("due", [False, True], ids=["by-hand", "due"])
def test_the_rules_in_use_reach_the_tunnel_and_the_routing_page(
    qtbot: QtBot, scene: Scene, due: bool
) -> None:
    """订阅自带 with 示例订阅 A, every 12 hours and updated 2 hours ago."""
    profiles = scene.view_models.profiles
    show_profile_rules(qtbot, scene)
    reloads, groups_read = scene.host.reloads, scene.groups_read
    scene.fetcher.body = MORE_RULES.encode()

    with qtbot.waitSignal(profiles.applied):
        if due:
            scene.now += 10 * 3600
            profiles.checkUpdates()
        else:
            profiles.updateProfile("sample-a")
        # Downloading and reloading happen off the window's thread.
        assert scene.host.reloads == reloads

    assert scene.host.reloads == reloads + 1
    assert scene.host.config is not None
    assert scene.host.config.config_yaml == MORE_RULES
    assert scene.host.config.options.tun_enabled  # reloaded in place
    # The routing page lists the rules the core runs now, and the proxies page and the
    # overview read the groups again.
    new_rules = [("DOMAIN-SUFFIX,example.com", "DIRECT"), ("MATCH", "节点选择")]
    qtbot.waitUntil(lambda: scene.rules() == new_rules)
    assert scene.rule_notes()["示例订阅 A"] == "2 条规则"
    assert scene.groups_read >= groups_read + 2
    assert rows(profiles, "model")[0].updated == "刚刚"


@pytest.mark.parametrize("due", [False, True], ids=["by-hand", "due"])
def test_nodes_in_the_pool_reach_the_tunnel_and_the_pages(
    qtbot: QtBot, scene: Scene, due: bool
) -> None:
    """我的方案, with 示例订阅 B in the pool: not the subscription whose rules 订阅自带 uses."""
    view_models = scene.view_models
    profiles, routing = view_models.profiles, view_models.routing
    use_plan(qtbot, scene)
    if due:
        profiles.setAutoUpdate("sample-b", True)  # 3 days old, so due
        qtbot.waitUntil(lambda: rows(profiles, "model")[1].autoUpdate)
    reloads, groups_read = scene.host.reloads, scene.groups_read
    scene.fetcher.body = MORE_NODES.encode()

    with qtbot.waitSignal(profiles.applied):
        if due:
            profiles.checkUpdates()
        else:
            profiles.updateProfile("sample-b")
        assert scene.host.reloads == reloads

    assert scene.host.reloads == reloads + 1
    assert scene.host.config is not None
    assert scene.host.config.composition is not None
    sources = {s.id: s.yaml for s in scene.host.config.composition.sources}
    assert sources["sample-b"] == MORE_NODES
    qtbot.waitUntil(lambda: prop(routing, "summary") == "使用「日常」 · 节点池 28 个节点")
    qtbot.waitUntil(lambda: prop(profiles, "summary") == "3 份订阅 · 节点池 28 个节点")
    # The proxies page names the new node's subscription, and reads the groups again.
    assert routing.pool_sources()[0]["香港 04"] == "示例订阅 B"
    assert scene.groups_read >= groups_read + 2


def test_the_text_stored_already_reloads_nothing(qtbot: QtBot, scene: Scene) -> None:
    """The tunnel runs that text: the update only dates the subscription and its usage."""
    profiles = scene.view_models.profiles
    show_profile_rules(qtbot, scene)
    reloads = scene.host.reloads
    with qtbot.assertNotEmitted(profiles.applied, wait=200):
        profiles.updateProfile("sample-a")
        qtbot.waitUntil(lambda: not prop(profiles, "activeUpdating"))
    assert scene.host.reloads == reloads
    assert rows(profiles, "model")[0].updated == "刚刚"
    assert scene.rules() == [("MATCH", "节点选择")]


def test_another_subscriptions_rules_show_without_a_reload(qtbot: QtBot, scene: Scene) -> None:
    """订阅自带 with 示例订阅 A: B's new rules run nowhere, but the page counts them."""
    profiles = scene.view_models.profiles
    show_profile_rules(qtbot, scene)
    reloads = scene.host.reloads
    scene.fetcher.body = MORE_RULES.encode()
    with qtbot.assertNotEmitted(profiles.applied, wait=200):
        profiles.updateProfile("sample-b")
        qtbot.waitUntil(lambda: scene.rule_notes()["示例订阅 B"] == "2 条规则")
    assert scene.host.reloads == reloads
    assert scene.rules() == [("MATCH", "节点选择")]
