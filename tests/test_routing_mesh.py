"""Mesh networks added in the app (docs/mesh.md §10): the routing page's rows, their status,
the dialog and the browser login, with the simulated host and the credential store in
memory."""

# The UI's Chinese text has fullwidth punctuation.
# ruff: noqa: RUF001

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, Qt, QUrl
from pytestqt.qtbot import QtBot

from solaris.host.base import (
    EasyTierSettings,
    MeshStatus,
    TailscaleSettings,
)
from solaris.models import plans
from solaris.models.routing import PlanRule
from solaris.models.store import ProfileStore
from solaris.viewmodels import routing_mesh
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.routing_mesh import MeshNetworks
from tests.support.credentials import MemoryCredentials
from tests.support.simulated import MESH_LOGIN, SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded, texts

NOW = datetime(2026, 10, 30, 12).timestamp()
LOGIN = MESH_LOGIN


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def networks_of(routing: RoutingViewModel) -> MeshNetworks:
    return routing._mesh_networks  # QML reads the property; Python has no converter for it


def host_of(view_models: ViewModels) -> SimulatedHost:
    host = view_models.host
    assert isinstance(host, SimulatedHost)
    return host


def row(networks: MeshNetworks, name: str) -> Any:
    return next(r for r in networks._rows.rows if r.name == name)


def values(**given: str) -> dict[str, str]:
    """The dialog's fields as QML hands them over."""
    return {name: given.get(name, "") for name in routing_mesh.FIELDS}


@pytest.fixture
def credentials() -> MemoryCredentials:
    return MemoryCredentials()


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The URLs the browser was asked to open."""
    urls: list[str] = []

    def open_url(url: QUrl) -> bool:
        urls.append(url.toString())
        return True

    monkeypatch.setattr(routing_mesh.QDesktopServices, "openUrl", open_url)
    return urls


@pytest.fixture
def planned(qtbot: QtBot, tmp_path: Path, credentials: MemoryCredentials) -> Iterator[ViewModels]:
    """Routing with the default plan; the tunnel runs and the groups tab is on screen."""
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(
        SimulatedHost(), store, SimulatedFetcher(), lambda: NOW, credentials=credentials
    )
    built.start()
    routing = built.routing
    qtbot.waitUntil(lambda: prop(built.overview, "connected"))
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    qtbot.waitUntil(lambda: prop(routing, "summary").endswith("27 个节点"))
    networks_of(routing).setWatching(True)
    yield built
    built.shutdown()


def add(qtbot: QtBot, networks: MeshNetworks, kind: str, **given: str) -> None:
    networks.openAdd()
    networks.setKind(kind)
    with qtbot.waitSignal(networks.saved):
        networks.save(values(**given))


def test_a_tailscale_network_logs_in_through_the_browser(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials, opened: list[str]
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    assert prop(networks, "available")
    assert not prop(networks, "hasRows")
    networks.openAdd()
    assert prop(networks, "title") == "添加网络"
    assert prop(networks, "values")["hostname"] == routing_mesh.device_name()
    with qtbot.waitSignal(networks.saved):
        networks.save(values(name="家里", login="browser", hostname="laptop"))
    # The core gets it with every composition; nothing of it is a secret.
    assert host.config is not None
    assert host.config.composition is not None
    (network,) = host.config.composition.mesh
    assert (network.name, network.settings) == ("家里", TailscaleSettings(hostname="laptop"))
    assert credentials.secrets == {}
    # The core has a login URL as soon as the network starts; the browser opens it once.
    host.mesh_states["家里"] = MeshStatus(
        "家里", f"mesh:{network.id}", "Tailscale", "needs_login", login_url=LOGIN
    )
    qtbot.waitUntil(lambda: opened == [LOGIN])
    assert (row(networks, "家里").status, row(networks, "家里").tone) == ("需要登录", "warning")
    # Logged in: online with the device's address.
    del host.mesh_states["家里"]
    qtbot.waitUntil(lambda: row(networks, "家里").status == "在线", timeout=5000)
    assert row(networks, "家里").caption == "Tailscale · 100.101.102.3 · 4 台设备"
    assert opened == [LOGIN]
    # Its rules: Tailscale's ranges and domain, to mesh:<id>.
    with qtbot.waitSignal(routing.applied):
        networks.addCommonRules(network.id)
    added = [r for r in prop(routing, "rules").rows if r.targetId == plans.MESH_PREFIX + network.id]
    assert [r.target for r in added] == ["家里", "家里"]
    assert any(r.proxy == "家里" for r in host.report.rules) if host.report else False


def test_logging_in_again_asks_the_core(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials, opened: list[str]
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="tskey-auth-1")
    network_id = row(networks, "家里").id
    qtbot.waitUntil(lambda: row(networks, "家里").status == "在线")
    assert opened == []  # a key needs no browser
    networks.login(network_id)
    qtbot.waitUntil(lambda: opened == [LOGIN])
    assert host.mesh_logins == ["家里"]
    # A URL that is no web page is not opened.
    host.mesh_states["家里"] = MeshStatus(
        "家里", f"mesh:{network_id}", "Tailscale", "needs_login", login_url="file:///etc/passwd"
    )
    qtbot.waitUntil(lambda: row(networks, "家里").status == "需要登录", timeout=5000)
    networks.login(network_id)
    assert prop(networks, "notice") == "登录链接不是网页地址，没有打开"
    assert opened == [LOGIN]


def test_secrets_stay_in_the_credential_store(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials, tmp_path: Path
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(
        qtbot,
        networks,
        "easytier",
        name="实验室",
        network_name="lab",
        network_secret="s3cret-value",
        peers="tcp://peer.example:11010, udp://other.example:11010",
    )
    network_id = row(networks, "实验室").id
    assert json.loads(credentials.secrets[f"mesh:{network_id}"]) == {
        "network_secret": "s3cret-value"
    }
    assert b"s3cret-value" not in (tmp_path / "solaris.db").read_bytes()
    assert host.config is not None
    assert host.config.composition is not None
    (network,) = host.config.composition.mesh
    assert network.settings == EasyTierSettings(
        "lab",
        "s3cret-value",
        ("tcp://peer.example:11010", "udp://other.example:11010"),
    )
    qtbot.waitUntil(lambda: row(networks, "实验室").status == "在线")
    assert row(networks, "实验室").caption == "EasyTier · 10.144.144.2 · 2 台设备"
    # Editing never shows the secret; leaving it empty keeps it.
    networks.openEdit(network_id)
    assert prop(networks, "title") == "编辑「实验室」"
    assert prop(networks, "values")["network_secret"] == ""
    assert prop(networks, "savedSecrets") == ["network_secret"]
    edited = values(**{**prop(networks, "values"), "name": "实验室 2"})
    with qtbot.waitSignal(networks.saved):
        networks.save(edited)
    (network,) = host.config.composition.mesh
    assert isinstance(network.settings, EasyTierSettings)
    assert (network.name, network.settings.network_secret) == ("实验室 2", "s3cret-value")


def test_the_dialog_checks_what_it_can(qtbot: QtBot, planned: ViewModels) -> None:
    networks = networks_of(planned.routing)
    add(qtbot, networks, "zerotier", name="公司", network="8056c2e21c000001")
    networks.openAdd()
    for kind, given, error in [
        ("tailscale", {"name": ""}, "请填写名称"),
        ("tailscale", {"name": "a,b"}, "名称里不能有逗号"),
        ("tailscale", {"name": "公司"}, "已有同名的网络"),
        ("tailscale", {"name": "x", "login": "key"}, "请填写授权密钥"),
        (
            "tailscale",
            {"name": "x", "control_url": "hs.example"},
            "控制服务器要以 https:// 或 http:// 开头",
        ),
        ("zerotier", {"name": "x", "network": "8056c2e2"}, "网络 ID 是 16 位十六进制数"),
        ("easytier", {"name": "x", "network_secret": "s", "peers": "tcp://p:1"}, "请填写网络名称"),
        ("easytier", {"name": "x", "network_name": "n", "peers": "tcp://p:1"}, "请填写网络密钥"),
        (
            "easytier",
            {"name": "x", "network_name": "n", "network_secret": "s"},
            "请填写至少一个对等节点",
        ),
        (
            "easytier",
            {
                "name": "x",
                "network_name": "n",
                "network_secret": "s",
                "peers": "tcp://p:1",
                "ipv4": "10.0.0.1",
            },
            "本机地址写成 10.144.144.2/24 这样",
        ),
    ]:
        networks.setKind(kind)
        networks.save(values(**given))
        assert prop(networks, "error") == error, (kind, given)


def test_zerotier_waits_for_its_admin_then_offers_its_range(
    qtbot: QtBot, planned: ViewModels
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(qtbot, networks, "zerotier", name="公司", network="8056C2E21C000001", via="auto")
    network_id = row(networks, "公司").id
    # The controller has not authorized the node yet.
    host.mesh_states["公司"] = MeshStatus(
        "公司", f"mesh:{network_id}", "ZeroTier", "waiting", node_id="a1b2c3d4e5"
    )
    qtbot.waitUntil(lambda: row(networks, "公司").status == "等待授权", timeout=5000)
    assert row(networks, "公司").caption == "ZeroTier · 本机 ID a1b2c3d4e5 · 经由 自动选择"
    assert host.effective is not None
    outbound = next(p for p in host.effective["proxies"] if p["name"] == "公司")
    assert (outbound["network"], outbound["dialer-proxy"]) == ("8056c2e21c000001", "自动选择")
    host.mesh_states["公司"] = MeshStatus(
        "公司", f"mesh:{network_id}", "ZeroTier", "online", addresses=("10.147.17.5/24",)
    )
    qtbot.waitUntil(lambda: row(networks, "公司").status == "在线", timeout=5000)
    assert row(networks, "公司").address == "10.147.17.5"
    editor = routing._editor
    with qtbot.waitSignal(editor.opened):
        networks.addCommonRules(network_id)
    assert prop(editor, "ipText") == "10.147.17.0/24"
    assert prop(editor, "target") == f"mesh:{network_id}"


def test_a_via_group_that_is_gone_goes_through_proxy(qtbot: QtBot, planned: ViewModels) -> None:
    networks = networks_of(planned.routing)
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="k", via="gone")
    assert row(networks, "家里").caption.endswith("经由 gone，暂经节点选择")


def test_removing_a_network_rejects_its_rules(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="tskey-auth-1")
    network_id = row(networks, "家里").id
    with qtbot.waitSignal(routing.applied):
        networks.addCommonRules(network_id)
    networks.requestRemove(network_id)
    assert prop(networks, "removalTitle") == "删除「家里」？"
    with qtbot.waitSignal(routing.applied):
        networks.confirmRemove()
    assert not prop(networks, "hasRows")
    assert credentials.secrets == {}
    assert host.config is not None
    assert host.config.composition is not None
    assert host.config.composition.mesh == ()
    rejected = [r for r in prop(routing, "rules").rows if r.targetId == f"mesh:{network_id}"]
    assert [(r.target, r.warning) for r in rejected] == [
        (f"mesh:{network_id}", "不存在，已拒绝连接")
    ] * 2


def test_removing_a_network_with_a_locked_store_still_takes_it_out(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials
) -> None:
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="tskey-auth-1")
    network_id = row(networks, "家里").id
    credentials.fail = True
    networks.requestRemove(network_id)
    with qtbot.waitSignal(routing.applied):
        networks.confirmRemove()
    assert not prop(networks, "hasRows")
    assert prop(networks, "notice") == ""
    assert host.config is not None
    assert host.config.composition is not None
    assert host.config.composition.mesh == ()
    # The secret stays behind in the store, which could not be written.
    assert f"mesh:{network_id}" in credentials.secrets


def test_a_network_without_its_secret_waits_for_it(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials
) -> None:
    """As after a restore on another device: the row is there, its secret is not."""
    routing, host = planned.routing, host_of(planned)
    networks = networks_of(routing)
    add(
        qtbot,
        networks,
        "easytier",
        name="实验室",
        network_name="lab",
        network_secret="s",
        peers="tcp://p.example:1",
    )
    network_id = row(networks, "实验室").id
    plan = routing.plan()
    assert plan is not None
    rule = PlanRule("r-lab", "ip", {"items": ["10.144.144.0/24"]}, f"mesh:{network_id}")
    with qtbot.waitSignal(routing.applied):
        routing.put_plan(replace(plan, rules=(rule, *plan.rules)), lambda: None, print)
    credentials.secrets.clear()
    planned.library.networks._secrets = None  # read again, as at the next start
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="k")
    assert (row(networks, "实验室").status, row(networks, "实验室").tone) == (
        "缺少网络密钥",
        "warning",
    )
    assert host.config is not None
    assert host.config.composition is not None
    assert [n.name for n in host.config.composition.mesh] == ["家里"]
    lab = next(r for r in prop(routing, "rules").rows if r.ruleId == "r-lab")
    assert (lab.target, lab.warning) == ("实验室", "不存在，已拒绝连接")


def test_stopped_networks_are_not_connected(qtbot: QtBot, planned: ViewModels) -> None:
    routing, overview = planned.routing, planned.overview
    networks = networks_of(routing)
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="k")
    qtbot.waitUntil(lambda: row(networks, "家里").status == "在线")
    overview.toggleConnection()
    qtbot.waitUntil(lambda: not prop(overview, "connected"))
    assert (row(networks, "家里").status, row(networks, "家里").tone) == ("未连接", "muted")
    assert row(networks, "家里").caption == "Tailscale"


def test_a_locked_credential_store_keeps_the_network_out(
    qtbot: QtBot, planned: ViewModels, credentials: MemoryCredentials
) -> None:
    networks = networks_of(planned.routing)
    credentials.fail = True
    networks.openAdd()
    networks.save(values(name="家里", login="key", auth_key="k"))
    qtbot.waitUntil(lambda: prop(networks, "error") != "")
    assert prop(networks, "error") == "操作失败：the keychain is locked"
    assert not prop(networks, "hasRows")


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("10.147.17.5/24", "10.147.17.0/24"), ("100.101.102.103/32", ""), ("junk", "")],
)
def test_network_range(prefix: str, expected: str) -> None:
    assert routing_mesh.network_range(prefix) == expected


def test_peers_are_split_on_commas_and_spaces() -> None:
    assert routing_mesh.peers_of("tcp://a:1, udp://b:2\ntcp://c:3，tcp://d:4") == (
        "tcp://a:1",
        "udp://b:2",
        "tcp://c:3",
        "tcp://d:4",
    )


@pytest.mark.parametrize("appearance", ["light", "dark"], indirect=True)
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
def test_the_section_and_its_dialog_load_without_qml_warnings(
    qtbot: QtBot, loaded: Loaded, opened: list[str]
) -> None:
    """The 组网 section with a network of each kind, and the dialog for each kind."""
    view_models = loaded.view_models
    routing = view_models.routing
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage("routing")
    with qtbot.waitSignal(routing.routingChanged):
        routing.setMode("plan")
    page = loaded.item(lambda i: i.metaObject().className().startswith("RoutingPage"))
    page.setProperty("tab", "groups")
    networks = networks_of(routing)
    content = loaded.window.contentItem()
    qtbot.waitUntil(lambda: bool({"添加网络…", "Add network…"} & texts(content)))
    add(qtbot, networks, "tailscale", name="家里", login="key", auth_key="k")
    add(qtbot, networks, "zerotier", name="公司", network="8056c2e21c000001")
    qtbot.waitUntil(lambda: "家里" in texts(content) and "公司" in texts(content))
    with qtbot.waitSignal(networks.opened):
        networks.openAdd()
    for kind in ("zerotier", "easytier", "tailscale"):
        networks.setKind(kind)
        qtbot.wait(20)
    loaded.key(qtbot, Qt.Key.Key_Escape)
    with qtbot.waitSignal(networks.opened):
        networks.openEdit(row(networks, "公司").id)
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert loaded.warnings == []
