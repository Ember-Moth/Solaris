"""The UI on the development service, step by step: import and connect, the pages that show
the core, modes and nodes, reloads, reconnecting, and the run options."""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from scripts.dev import DevService
from solaris.host.base import HostError, RunOptions, TunnelConfig
from solaris.host.service import ServiceHost
from tests.e2e.app import DIRECT_PROFILE, App, nodes_profile
from tests.e2e.servers import free_port, listening_port
from tests.e2e.unix import unix_opener
from tests.support.simulated import SAMPLE_CONFIG, SimulatedHost

pytestmark = pytest.mark.e2e

USAGE = "upload=1073741824; download=4294967296; total=21474836480; expire=1924905600"


def listening(pid: int) -> set[str]:
    """The TCP addresses a process listens on."""
    done = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {line[1:] for line in done.stdout.splitlines() if line.startswith("n")}


def serve_subscription(app: App, text: str) -> str:
    web = app.network.direct
    web.subscription = text.encode()
    web.userinfo = USAGE
    web.filename = "e2e-sub.yaml"
    return web.url("/sub")


def connected_on_nodes(app: App) -> None:
    """Import the direct profile and the nodes profile, use the nodes and connect."""
    app.import_file(app.write("direct.yaml", DIRECT_PROFILE))
    app.import_file(app.write("nodes.yaml", nodes_profile(app.network)))
    app.view_models.profiles.useRules(app.profile_id("nodes"))
    app.until(lambda: app.prop(app.view_models.profiles, "activeName") == "nodes")
    app.connect()


def test_import_check_connect_and_proxy(app: App) -> None:
    """Import a file and a subscription, the service checks them, connect, and
    curl goes through the mixed port. The development service clears what would reach
    beyond this machine's loopback."""
    vm, network = app.view_models, app.network
    app.until(lambda: app.prop(vm.shell, "serviceAvailable"))
    assert app.prop(vm.shell, "serviceDetail").endswith("· 协议 v1")

    # A profile with only DIRECT is a valid profile.
    app.import_file(app.write("direct.yaml", DIRECT_PROFILE))
    (direct,) = app.rows(vm.profiles)
    assert (direct.name, direct.source, direct.active) == ("direct", "本地文件 · direct.yaml", True)

    # A subscription with usage, served by a local "provider".
    app.import_url(serve_subscription(app, nodes_profile(network)))
    subscription = app.rows(vm.profiles)[-1]
    assert subscription.name == "e2e-sub"
    assert subscription.source == "订阅 · 127.0.0.1 · 2 个节点"
    assert subscription.hasQuota
    assert subscription.usage == "5.0 / 20.0 GB"
    assert subscription.expiry != "—"

    # A config the core rejects is not imported.
    app.view_models.profiles.importFile(app.write("broken.yaml", "proxies: [\n").as_uri())
    app.until(lambda: app.prop(vm.profiles, "hasError"))
    assert app.prop(vm.profiles, "summary").startswith("配置无效：")  # noqa: RUF001
    assert len(app.rows(vm.profiles)) == 2
    vm.shell.setPage("profiles")
    app.screenshot("profiles")

    vm.profiles.useRules(subscription.id)
    app.until(lambda: app.prop(vm.profiles, "activeName") == "e2e-sub")
    app.connect()
    assert app.curl(network.proxied.url()) == "proxied"
    assert app.curl(network.direct.url()) == "direct"
    assert network.node_a.targets == [f"127.0.0.1:{network.proxied.port}"]
    assert network.node_b.targets == []

    # No TUN, no LAN, no redirect port, no controller: the mixed port on loopback only.
    assert listening(app.service.pid) == {f"127.0.0.1:{app.mixed_port}"}
    assert not app.prop(vm.overview, "tunEnabled")
    assert not (app.service.home / "controller.sock").exists()


def test_pages_show_the_core(app: App) -> None:
    """Overview, proxies, connections and logs read the core."""
    vm, network = app.view_models, app.network
    connected_on_nodes(app)
    stream = app.stream(seconds=20)
    app.until(lambda: app.prop(vm.overview, "connectionsLink") == "全部 1 个")
    destination = f"127.0.0.1:{network.proxied.port}"

    # Overview: status, traffic and the busiest connection with its route.
    overview = vm.overview
    assert app.prop(overview, "statusTitle") == "已连接"
    # The development service takes over nothing: only the mixed port serves (D26).
    assert app.prop(overview, "statusDetail").startswith(f"混合端口 {app.mixed_port} · 已运行 ")
    assert (app.prop(overview, "nodeGroup"), app.prop(overview, "nodeName")) == (
        "节点选择",
        "node-a",
    )
    assert app.prop(overview, "nodeDelay") == ""  # not tested yet
    app.until(lambda: app.prop(overview, "downTotal") not in ("—", "0 B"))
    app.until(lambda: app.prop(overview, "downRate") not in ("—", "0 B/s"))
    (recent,) = app.prop(overview, "recent")
    assert (recent["host"], recent["route"]) == (destination, "节点选择 · node-a")
    app.until(lambda: app.prop(overview, "recent")[0]["rate"] != "0 B/s")
    rates = set()
    for _ in range(6):
        app.qtbot.wait(500)
        rates.add(app.prop(overview, "recent")[0]["rate"])
    assert "0 B/s" not in rates  # the stream never stops
    app.screenshot("overview")

    # Proxies: the groups in configuration order, GLOBAL last; a delay test.
    vm.shell.setPage("proxies")
    proxies = vm.proxies
    app.until(lambda: [g.name for g in app.prop(proxies, "groups").rows] == ["节点选择", "GLOBAL"])
    nodes = app.prop(proxies, "nodes")
    assert [(n.name, n.type, n.selected) for n in nodes.rows] == [
        ("node-a", "Http", True),
        ("node-b", "Http", False),
        ("DIRECT", "Direct", False),
    ]
    assert {n.delay for n in nodes.rows} == {"—"}
    proxies.testCurrentGroup()
    app.until(lambda: not app.prop(proxies, "testing"))
    assert [n.level for n in nodes.rows] == ["good", "good", "none"]
    assert all(n.delay.endswith(" ms") for n in nodes.rows[:2])
    app.until(lambda: app.prop(overview, "nodeDelay").endswith(" ms"))
    app.screenshot("proxies")

    # Connections: the stream, closed alone; then two more, closed together.
    vm.shell.setPage("connections")
    connections = vm.connections
    app.until(lambda: len(app.rows(connections)) == 1)
    (row,) = app.rows(connections)
    assert (row.host, row.protocol, row.chain) == (destination, "TCP", "节点选择 › node-a")  # noqa: RUF001
    assert row.rule == f"DST-PORT,{network.proxied.port}"
    app.until(lambda: app.rows(connections)[0].downRate != "0 B/s")
    app.screenshot("connections")
    connections.closeConnection(row.id)
    assert stream.done.wait(5)
    app.until(lambda: not app.rows(connections))
    streams = [app.stream(seconds=20), app.stream(seconds=20)]
    app.until(lambda: len(app.rows(connections)) == 2)
    connections.closeAll()
    assert all(s.done.wait(5) for s in streams)
    app.until(lambda: not app.rows(connections))
    assert app.prop(connections, "emptyText") != ""

    # Logs: mihomo's lines, the level filter and pause.
    vm.shell.setPage("logs")
    logs = vm.logs
    app.until(lambda: app.prop(logs, "count") > 0)
    levels = {entry.level for entry in app.prop(logs, "model").entries}
    assert "info" in levels
    app.screenshot("logs")
    logs.setLevel("warning")
    assert {e.level for e in app.prop(logs, "model").entries} <= {"warning", "error"}
    logs.setLevel("info")
    logs.togglePause()
    shown = app.prop(logs, "count")
    vm.profiles.useRules(app.profile_id("direct"))  # a reload logs a few lines
    app.until(lambda: app.prop(logs, "status").startswith("已暂停 · "))
    assert app.prop(logs, "count") == shown
    logs.togglePause()
    assert app.prop(logs, "count") > shown
    assert app.warnings == []


def test_mode_and_node_steer_traffic(app: App) -> None:
    """The node and the mode chosen in the UI decide which way requests go."""
    vm, network = app.view_models, app.network
    connected_on_nodes(app)
    proxied = f"127.0.0.1:{network.proxied.port}"
    direct = f"127.0.0.1:{network.direct.port}"

    vm.shell.setPage("proxies")
    app.until(lambda: app.prop(vm.proxies, "currentGroup") == "节点选择")
    vm.proxies.selectProxy("node-b")
    app.until(lambda: app.prop(vm.overview, "nodeName") == "node-b")
    assert app.curl(network.proxied.url()) == "proxied"
    assert (network.node_a.targets, network.node_b.targets) == ([], [proxied])

    vm.overview.setMode("global")
    app.until(lambda: app.prop(vm.overview, "mode") == "global")
    vm.proxies.selectGroup("GLOBAL")
    vm.proxies.selectProxy("node-a")
    app.until(lambda: app.prop(vm.proxies, "groups").rows[-1].now == "node-a")
    assert app.curl(network.direct.url()) == "direct"
    assert network.node_a.targets == [direct]

    vm.overview.setMode("direct")
    app.until(lambda: app.prop(vm.overview, "mode") == "direct")
    assert app.curl(network.proxied.url()) == "proxied"
    assert (len(network.node_a.targets), len(network.node_b.targets)) == (1, 1)

    vm.overview.setMode("rule")
    app.until(lambda: app.prop(vm.overview, "mode") == "rule")
    assert app.curl(network.proxied.url()) == "proxied"
    assert network.node_b.targets == [proxied, proxied]


def test_development_mode_opens_the_mixed_port_only(app: App) -> None:
    """The development service never takes over the machine's traffic. The UI does not ask
    it to (TAKEOVER_NONE), so connecting with the settings' defaults, both on, works; asked
    directly, the service still refuses."""
    vm = app.view_models
    overview, store = vm.overview, vm.settings_store
    assert (store.current.tun, store.current.system_proxy) == (True, True)
    assert app.prop(overview, "takeover") == "none"
    connected_on_nodes(app)
    assert not app.prop(overview, "tunEnabled")
    assert not app.prop(overview, "systemProxyEnabled")
    assert listening(app.service.pid) == {f"127.0.0.1:{app.mixed_port}"}
    assert app.curl(app.network.direct.url()) == "direct"

    async def refusals() -> list[str]:
        host = ServiceHost(unix_opener(str(app.service.socket)))
        codes = []
        try:
            port = app.mixed_port
            for options in (
                RunOptions(tun_enabled=True, mixed_port=port),
                RunOptions(system_proxy_enabled=True, mixed_port=port),
            ):
                try:
                    await host.reload(TunnelConfig(DIRECT_PROFILE, options=options))
                except HostError as error:
                    codes.append(error.code)
            try:
                await host.set_system_proxy(True)
            except HostError as error:
                codes.append(error.code)
        finally:
            await host.close()
        return codes

    assert asyncio.run(refusals()) == ["unsupported_method"] * 3
    assert app.prop(overview, "connected")
    assert app.curl(app.network.direct.url()) == "direct"


def test_reloads_keep_connections(app: App) -> None:
    """Editing an override, updating the subscription in use and switching profiles reload
    in place: what changed takes effect for new connections, open ones carry on
    (docs/routing.md §5.4)."""
    vm, network = app.view_models, app.network
    profiles = vm.profiles
    app.import_file(app.write("direct.yaml", DIRECT_PROFILE))
    app.import_url(serve_subscription(app, nodes_profile(network)))
    profiles.useRules(app.profile_id("e2e-sub"))
    app.until(lambda: app.prop(profiles, "activeName") == "e2e-sub")
    app.connect()
    stream = app.stream(seconds=8)
    app.until(lambda: stream.received > 0)

    # Only the rules change: the direct server now goes through node-b.
    with app.qtbot.waitSignal(profiles.overrideOpened):
        profiles.editOverride(app.profile_id("e2e-sub"))
    profiles.saveOverride(f"prepend-rules:\n  - DST-PORT,{network.direct.port},node-b\n")
    app.until(lambda: not app.prop(profiles, "overrideSaving"))
    assert app.prop(profiles, "overrideError") == ""
    assert app.curl(network.direct.url()) == "direct"
    assert network.node_b.targets == [f"127.0.0.1:{network.direct.port}"]

    # The subscription in use changes its nodes and groups: node-a is gone.
    network.direct.subscription = (
        nodes_profile(network)
        .replace("[node-a, node-b, DIRECT]", "[node-b, DIRECT]")
        .replace(
            f"  - {{name: node-a, type: http, server: 127.0.0.1, port: {network.node_a.port}}}\n",
            "",
        )
        .encode()
    )
    profiles.updateProfile(app.profile_id("e2e-sub"))
    app.until(lambda: app.prop(vm.overview, "nodeName") == "node-b")
    assert app.curl(network.proxied.url()) == "proxied"
    assert network.node_b.targets[-1] == f"127.0.0.1:{network.proxied.port}"

    # Another profile altogether.
    profiles.useRules(app.profile_id("direct"))
    app.until(lambda: app.prop(profiles, "activeName") == "direct")
    app.until(lambda: app.prop(vm.overview, "nodeName") == "—")
    tunnels = len(network.node_a.targets) + len(network.node_b.targets)
    assert app.curl(network.proxied.url()) == "proxied"
    assert len(network.node_a.targets) + len(network.node_b.targets) == tunnels

    # All three reloads left the first download alone. It runs 8 s from its start.
    finished = stream.done.wait(max(stream.started + 8 + 10 - time.monotonic(), 0))
    assert finished, app.describe(stream, network.proxied)
    assert stream.error == ""
    assert stream.received > 80 * 1024  # 8 s of 1 KiB every 0.1 s, and the headers
    assert app.prop(vm.overview, "connected")


def test_reconnect_and_service_restart(app: App) -> None:
    """Disconnecting and connecting again, and the service going away and coming back."""
    vm = app.view_models
    overview, shell = vm.overview, vm.shell
    connected_on_nodes(app)
    url = app.network.direct.url()

    overview.toggleConnection()
    app.until(lambda: app.prop(overview, "statusTitle") == "未连接")
    assert app.curl(url) != "direct"
    app.connect()
    assert app.curl(url) == "direct"

    # The service goes away: every page says so, nothing hangs.
    app.service.stop()
    app.until(lambda: "连接" in app.prop(overview, "statusDetail"))
    shell.refreshService()
    app.until(lambda: not app.prop(shell, "serviceAvailable"))
    vm.shell.setPage("logs")
    app.until(lambda: app.prop(vm.logs, "statusTone") == "danger")

    # It comes back stopped, as after a crash; the UI picks it up again.
    app.service.start()
    shell.refreshService()
    app.until(lambda: app.prop(shell, "serviceAvailable"))
    app.until(lambda: app.prop(overview, "statusTitle") == "未连接")
    app.until(lambda: app.prop(overview, "statusDetail") == "")
    assert app.prop(overview, "statusTitle") == "未连接"
    app.until(lambda: app.prop(vm.logs, "statusTone") == "")
    app.connect()
    assert app.curl(url) == "direct"
    vm.shell.setPage("proxies")
    app.until(lambda: app.prop(vm.proxies, "currentGroup") == "节点选择")
    vm.shell.setPage("connections")
    stream = app.stream(seconds=3)
    app.until(lambda: len(app.rows(vm.connections)) == 1)
    assert stream.done.wait(5)


def test_mixed_port_and_lan(app: App) -> None:
    """The settings page's mixed port and LAN access on the running tunnel and the next."""
    vm = app.view_models
    settings, overview = vm.settings, vm.overview
    connected_on_nodes(app)
    url = app.network.direct.url()
    port = app.mixed_port

    # A port another program holds: the old one stays, the reason shows under the row.
    vm.shell.setPage("settings")
    holder, busy = listening_port()
    with holder:
        settings.setMixedPort(str(busy))
        app.until(lambda: app.prop(settings, "portError"))
        assert app.prop(settings, "portError") == (
            f"配置未能生效：listen tcp 127.0.0.1:{busy}: bind: address already in use"  # noqa: RUF001
        )
        assert app.prop(settings, "mixedPort") == port
        assert app.curl(url) == "direct"
        assert listening(app.service.pid) == {f"127.0.0.1:{port}"}
        app.screenshot("settings-port-in-use")

    # A free port moves the tunnel.
    free = free_port()
    settings.setMixedPort(str(free))
    app.until(lambda: app.prop(settings, "mixedPort") == free)
    assert app.prop(settings, "portError") == ""
    assert app.curl(url) == "direct"
    assert listening(app.service.pid) == {f"127.0.0.1:{free}"}

    # LAN access: stored and sent, yet the development service stays on loopback.
    settings.setAllowLan(True)
    app.until(lambda: app.prop(settings, "allowLan"))
    assert app.prop(settings, "lanError") == ""
    assert listening(app.service.pid) == {f"127.0.0.1:{free}"}

    # Stopped, a busy port fails the next connection, which says why.
    overview.toggleConnection()
    app.until(lambda: app.prop(overview, "statusTitle") == "未连接")
    holder, busy = listening_port()
    with holder:
        settings.setMixedPort(str(busy))
        app.until(lambda: app.prop(settings, "mixedPort") == busy)
        overview.setConnected(True)
        app.until(lambda: app.prop(overview, "connectionFailed"))
        assert app.prop(overview, "connectionDetail") == (
            f"配置未能生效：listen tcp 127.0.0.1:{busy}: bind: address already in use"  # noqa: RUF001
        )
        assert not app.prop(overview, "connectionOn")
        app.until(lambda: app.prop(overview, "statusTitle") == "连接失败")
    settings.setMixedPort(str(free))
    app.until(lambda: app.prop(settings, "mixedPort") == free)
    app.connect()
    assert app.curl(url) == "direct"


# Configs the simulated host must judge as the core does: the code, and the message where
# the UI shows it.
VERDICTS = {
    "sample": TunnelConfig(SAMPLE_CONFIG),
    "direct only": TunnelConfig(DIRECT_PROFILE),
    "empty": TunnelConfig("{}\n"),
    "groups name proxies an override removed": TunnelConfig(SAMPLE_CONFIG, ("proxies: []\n",)),
    "a rule names no proxy": TunnelConfig("rules: ['MATCH,NOWHERE']\n"),
    "an override adds a rule": TunnelConfig(DIRECT_PROFILE, ("prepend-rules: ['MATCH,REJECT']\n",)),
}


@pytest.mark.parametrize("name", VERDICTS)
def test_simulated_host_judges_configs_as_the_core(service: DevService, name: str) -> None:
    async def verdict(host: ServiceHost | SimulatedHost) -> str:
        try:
            await host.validate(VERDICTS[name])
        except HostError as error:
            return f"{error.code}: {error.message}"
        return "valid"

    async def both() -> tuple[str, str]:
        real = ServiceHost(unix_opener(str(service.socket)))
        try:
            return await verdict(real), await verdict(SimulatedHost())
        finally:
            await real.close()

    real, simulated = asyncio.run(both())
    assert simulated == real
