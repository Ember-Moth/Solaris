"""The mesh networks the user adds in the app, on the routing page's groups tab
(docs/mesh.md §10.7): a row for each with how it stands, and the dialog that adds or
changes one.

Saving goes to the library, which has the core compose the plan in use with the network
before it stores the network and its secrets and reloads a running tunnel. How the
networks stand comes from the running core (GetMeshStatus) every few seconds while the tab
is on screen. A Tailscale network that logs in through the browser opens the login page
once the core has its URL; the row's menu opens it again later, or switches accounts.
"""

from __future__ import annotations

import ipaddress
import platform
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import (
    MESH_KINDS,
    EasyTierSettings,
    HostError,
    MeshNetwork,
    MeshSettings,
    MeshStatus,
    TailscaleSettings,
    ZeroTierSettings,
)
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import mesh
from solaris.models.library import ProfileLibrary, Routing
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

KIND_LABELS = {"tailscale": "Tailscale", "zerotier": "ZeroTier", "easytier": "EasyTier"}
assert tuple(KIND_LABELS) == MESH_KINDS
LOGIN_BROWSER, LOGIN_KEY = "browser", "key"
LOGIN_METHODS = {
    LOGIN_BROWSER: QT_TRANSLATE_NOOP("MeshNetworks", "浏览器登录"),
    LOGIN_KEY: QT_TRANSLATE_NOOP("MeshNetworks", "授权密钥"),
}
POLL_MS = 3000
# The row's state and how it reads: "" as text, "muted" for the quiet ones, "warning" when
# the network waits for the user or failed.
TONE_NORMAL, TONE_MUTED, TONE_WARNING = "", "muted", "warning"
STATES = {
    "starting": (QT_TRANSLATE_NOOP("MeshNetworks", "连接中…"), TONE_MUTED),
    "needs_login": (QT_TRANSLATE_NOOP("MeshNetworks", "需要登录"), TONE_WARNING),
    "waiting": (QT_TRANSLATE_NOOP("MeshNetworks", "等待授权"), TONE_WARNING),
    "online": (QT_TRANSLATE_NOOP("MeshNetworks", "在线"), TONE_NORMAL),
    "failed": (QT_TRANSLATE_NOOP("MeshNetworks", "出错"), TONE_WARNING),
}
ZEROTIER_ID = re.compile(r"[0-9A-Fa-f]{16}")
# The dialog's fields, as QML hands them over: every kind's, by name.
FIELDS: tuple[str, ...] = (
    "name",
    "via",
    "login",
    "auth_key",
    "control_url",
    "hostname",
    "network",
    "identity_secret",
    "network_name",
    "network_secret",
    "peers",
    "ipv4",
)


@dataclass(frozen=True)
class NetworkRow:
    id: str  # the network's id
    name: str
    caption: str
    status: str
    tone: str  # one of the TONE_ values
    kind: str
    canLogin: bool  # Tailscale: to log in, or to switch accounts
    address: str  # the device's first address in the network; "" while it has none

    @property
    def networkId(self) -> str:
        return self.id


def _fields(row_type: type, id_role: str) -> list[str]:
    return [id_role if name == "id" else name for name in row_type.__dataclass_fields__]


def device_name() -> str:
    """This machine's name as a tailnet or an EasyTier network shows it."""
    name = platform.node().removesuffix(".local")
    return re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-").lower() or "solaris"


def peers_of(value: str) -> tuple[str, ...]:
    """Peer URIs as the dialog takes them: separated by commas, spaces or lines."""
    return tuple(part for part in re.split(r"[\s,，]+", value) if part)


def bare_address(prefix: str) -> str:
    """The address of a CIDR the core reports: 100.101.102.103/32 → 100.101.102.103."""
    return prefix.partition("/")[0]


def network_range(prefix: str) -> str:
    """The network a device's address is in: 10.147.17.5/24 → 10.147.17.0/24; "" for a
    one-address prefix, which says nothing of the network."""
    try:
        interface = ipaddress.ip_interface(prefix)
    except ValueError:
        return ""
    if interface.network.prefixlen == interface.max_prefixlen:
        return ""
    return str(interface.network)


@QmlElement
@QmlUncreatable("MeshNetworks is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class MeshNetworks(QObject):
    changed = Signal()
    opened = Signal()  # the view opens the dialog
    saved = Signal()  # the network is stored; the view closes the dialog
    removalRequested = Signal()  # removing a network awaits confirmation

    def __init__(
        self,
        routing: RoutingViewModel,
        runner: AsyncRunner,
        library: ProfileLibrary,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._routing = routing
        self._runner = runner
        self._library = library
        self._rows = KeyedListModel(_fields(NetworkRow, "networkId"), self)
        self._statuses: dict[str, MeshStatus] = {}  # by mesh id, from the running core
        self._running = False
        self._watching = False
        self._polling = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._notice = ""  # why the last action on a row failed
        # A network whose login page opens once the core has its URL.
        self._login_pending = ""
        # The dialog: the network edited, None while adding one.
        self._editing: MeshNetwork | None = None
        self._kind = MESH_KINDS[0]
        self._error = ""
        self._saving = False
        self._removing = ""
        routing.changed.connect(self._sync)
        self._sync()

    def retranslate(self) -> None:
        self._sync()

    # -- what the row shows ------------------------------------------------------------------
    def _networks(self) -> tuple[MeshNetwork, ...]:
        return self._routing.networks()

    def _network(self, network_id: str) -> MeshNetwork | None:
        return next((n for n in self._networks() if n.id == network_id), None)

    def _outbound_name(self, network_id: str) -> str:
        """The network's outbound in the configuration, which the core knows it by."""
        report = self._routing.report()
        target = mesh.target(network_id)
        found = next((m for m in report.mesh if m.id == target), None) if report else None
        return found.name if found else ""

    def _state(self, network: MeshNetwork) -> tuple[str, str]:
        status = self._statuses.get(mesh.target(network.id))
        if not mesh.runnable(self._with_stored_secrets(network)):
            return self.tr("缺少网络密钥"), TONE_WARNING
        if not self._running or not self._routing.routes_with_plan():
            return self.tr("未连接"), TONE_MUTED
        if status is None:
            return self.tr("连接中…"), TONE_MUTED
        label, tone = STATES.get(status.state, STATES["starting"])
        return self.tr(label), tone

    def _with_stored_secrets(self, network: MeshNetwork) -> MeshNetwork:
        """The network with a stand-in for each secret the credential store holds."""
        stored = self._routing.network_secrets().get(network.id, frozenset())
        return mesh.with_secrets(network, dict.fromkeys(stored, "*"))

    def _caption(self, network: MeshNetwork) -> str:
        parts = [KIND_LABELS[network.kind]]
        status = self._statuses.get(mesh.target(network.id)) if self._running else None
        if status is not None and status.state == "online":
            if status.addresses:
                parts.append(bare_address(status.addresses[0]))
            parts.append(self.tr("%n 台设备", "", status.peer_count))
        elif status is not None and status.state == "waiting" and status.node_id:
            parts.append(self.tr("本机 ID {node}").format(node=status.node_id))
        elif status is not None and status.state == "failed" and status.error:
            parts.append(status.error)
        if network.via:
            via = text.target_label(network.via, self._routing.plan(), self._routing.report())
            if self._via_empty(network.id):
                parts.append(self.tr("经由 {via}，暂经节点选择").format(via=via))
            else:
                parts.append(self.tr("经由 {via}").format(via=via))
        return " · ".join(parts)

    def _via_empty(self, network_id: str) -> bool:
        report = self._routing.report()
        target = mesh.target(network_id)
        return report is not None and any(
            w.entry_id == target and w.reason == "via_empty" for w in report.warnings
        )

    def _sync(self) -> None:
        rows = []
        for network in self._networks():
            status, tone = self._state(network)
            found = self._statuses.get(mesh.target(network.id)) if self._running else None
            rows.append(
                NetworkRow(
                    id=network.id,
                    name=self._outbound_name(network.id) or network.name,
                    caption=self._caption(network),
                    status=status,
                    tone=tone,
                    kind=network.kind,
                    canLogin=network.kind == "tailscale",
                    address=bare_address(found.addresses[0]) if found and found.addresses else "",
                )
            )
        self._rows.sync(rows)
        self._update_timer()
        self.changed.emit()

    @Property(QObject, constant=True)
    def rows(self) -> KeyedListModel:
        return self._rows

    @Property(bool, notify=changed)
    def available(self) -> bool:
        """Networks run with a plan, so the section shows under 我的方案 only (§1)."""
        return self._routing.routes_with_plan() and self._routing.plan() is not None

    @Property(bool, notify=changed)
    def hasRows(self) -> bool:
        return bool(self._rows.rows)

    @Property(str, notify=changed)
    def notice(self) -> str:
        return self._notice

    # -- how they stand ----------------------------------------------------------------------
    def set_running(self, running: bool) -> None:
        if running == self._running:
            return
        self._running = running
        if not running:
            self._statuses = {}
        self._sync()

    @Slot(bool)
    def setWatching(self, watching: bool) -> None:
        """The groups tab is on screen, or no longer is."""
        if watching != self._watching:
            self._watching = watching
            self._update_timer()

    def _update_timer(self) -> None:
        wanted = (
            self._running
            and self._watching
            and self._routing.routes_with_plan()
            and bool(self._networks() or self._routing.has_mesh())
        )
        if wanted and not self._timer.isActive():
            self._timer.start()
            self._poll()
        elif not wanted and self._timer.isActive():
            self._timer.stop()

    @Slot()
    def _poll(self) -> None:
        if self._polling:
            return
        self._polling = True

        def read(statuses: list[MeshStatus]) -> None:
            self._polling = False
            self._statuses = {s.id: s for s in statuses if s.id}
            self._open_pending_login()
            self._sync()

        def failed(error: BaseException) -> None:
            self._polling = False
            if isinstance(error, HostError) and error.code == "not_running":
                self._statuses = {}
                self._sync()

        self._runner.submit(self._library.mesh_status(), read, failed)

    def status_of(self, mesh_id: str) -> MeshStatus | None:
        """How the running core reports a mesh outbound, by its plan id."""
        return self._statuses.get(mesh_id) if self._running else None

    # -- the row's menu ----------------------------------------------------------------------
    @Slot(str)
    def login(self, network_id: str) -> None:
        """Open the network's login page: the one the core has, or a new login."""
        self._notice = ""
        status = self._statuses.get(mesh.target(network_id))
        if status is not None and status.state == "needs_login" and status.login_url:
            self._open_url(status.login_url)
            return
        name = self._outbound_name(network_id)
        if not self._running or not name:
            self._notice = self.tr("连接后才能登录")
            self.changed.emit()
            return

        def failed(error: BaseException) -> None:
            self._notice = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.login_mesh(name), self._open_url, failed)

    def _open_pending_login(self) -> None:
        if not self._login_pending:
            return
        status = self._statuses.get(mesh.target(self._login_pending))
        if status is None or status.state == "starting":
            return
        self._login_pending = ""
        if status.state == "needs_login" and status.login_url:
            self._open_url(status.login_url)

    def _open_url(self, url: str) -> None:
        """Open a login URL in the browser: only a web page (docs/mesh.md §10.6)."""
        address = QUrl(url)
        if address.scheme() not in ("https", "http") or not address.host():
            self._notice = self.tr("登录链接不是网页地址，没有打开")
            self.changed.emit()
            return
        QDesktopServices.openUrl(address)

    @Slot(str)
    def copyAddress(self, network_id: str) -> None:
        row = next((r for r in self._rows.rows if r.id == network_id), None)
        if row is not None and row.address:
            QGuiApplication.clipboard().setText(row.address)

    @Slot(str)
    def addCommonRules(self, network_id: str) -> None:
        """Tailscale's ranges and domain; for ZeroTier and EasyTier, the rule dialog with the
        range the device's address is in."""
        target = mesh.target(network_id)
        status = self.status_of(target)
        ranges = [r for a in (status.addresses if status else ()) if (r := network_range(a))]
        self._routing.add_mesh_rules(target, ranges[:1])

    @Slot(str)
    def showRules(self, network_id: str) -> None:
        self._routing.showMeshRules(mesh.target(network_id))

    # -- the dialog --------------------------------------------------------------------------
    @Slot()
    def openAdd(self) -> None:
        self._editing = None
        self._kind = MESH_KINDS[0]
        self._error = ""
        self.changed.emit()
        self.opened.emit()

    @Slot(str)
    def openEdit(self, network_id: str) -> None:
        network = self._network(network_id)
        if network is None:
            return
        self._editing = network
        self._kind = network.kind
        self._error = ""
        self.changed.emit()
        self.opened.emit()

    @Property(bool, notify=changed)
    def editing(self) -> bool:
        return self._editing is not None

    @Property(str, notify=changed)
    def title(self) -> str:
        if self._editing is None:
            return self.tr("添加网络")
        return self.tr("编辑「{name}」").format(name=self._editing.name)

    @Property(str, notify=changed)
    def kind(self) -> str:
        return self._kind

    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def kindOptions(self) -> list[dict[str, str]]:
        return [{"value": kind, "label": label} for kind, label in KIND_LABELS.items()]

    @Slot(str)
    def setKind(self, kind: str) -> None:
        if self._editing is None and kind in MESH_KINDS and kind != self._kind:
            self._kind = kind
            self._error = ""
            self.changed.emit()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def loginOptions(self) -> list[dict[str, str]]:
        return [{"value": value, "label": self.tr(label)} for value, label in LOGIN_METHODS.items()]

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def viaOptions(self) -> list[dict[str, object]]:
        groups = text.group_options(self._routing.plan(), self._routing.report())
        if groups:
            groups[0] = {**groups[0], "separator": True}
        return [{"value": "", "label": self.tr("直连"), "separator": False}, *groups]

    @Property("QVariantMap", notify=changed)  # pyright: ignore[reportArgumentType]
    def values(self) -> dict[str, str]:
        """What the dialog's fields start with. Secrets are never shown: their fields start
        empty, and ``savedSecrets`` names those the credential store holds."""
        values: dict[str, str] = dict.fromkeys(FIELDS, "")
        values["login"] = LOGIN_BROWSER
        network = self._editing
        if network is None:
            values["hostname"] = device_name()
            return values
        values |= {"name": network.name, "via": network.via}
        settings = network.settings
        if isinstance(settings, TailscaleSettings):
            values |= {"control_url": settings.control_url, "hostname": settings.hostname}
            if "auth_key" in self._saved_secrets():
                values["login"] = LOGIN_KEY
        elif isinstance(settings, ZeroTierSettings):
            values["network"] = settings.network
        elif isinstance(settings, EasyTierSettings):
            values |= {
                "network_name": settings.network_name,
                "peers": ", ".join(settings.peers),
                "ipv4": settings.ipv4,
                "hostname": settings.hostname,
            }
        return values

    def _saved_secrets(self) -> frozenset[str]:
        if self._editing is None:
            return frozenset()
        return self._routing.network_secrets().get(self._editing.id, frozenset())

    @Property("QStringList", notify=changed)  # pyright: ignore[reportArgumentType]
    def savedSecrets(self) -> list[str]:
        return sorted(self._saved_secrets())

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._saving or bool(self._routing.property("busy"))

    @Slot("QVariantMap")
    def save(self, values: dict[str, Any]) -> None:
        """Add or change the network as the dialog has it; a secret left empty keeps the
        saved one, unless Tailscale no longer logs in with its key."""
        if self._saving:
            return
        given = {name: str(values.get(name) or "").strip() for name in FIELDS}
        settings, problem = self._settings(given)
        name = given["name"]
        taken = {
            n.name for n in self._networks() if self._editing is None or n.id != self._editing.id
        }
        if not name:
            problem = self.tr("请填写名称")
        elif "," in name:
            problem = self.tr("名称里不能有逗号")
        elif name in taken:
            problem = self.tr("已有同名的网络")
        if problem or settings is None:
            self._error = problem
            self.changed.emit()
            return
        keep = set(self._saved_secrets())
        if isinstance(settings, TailscaleSettings) and given["login"] != LOGIN_KEY:
            keep.discard("auth_key")
        network_id = self._editing.id if self._editing else mesh.new_network_id()
        network = MeshNetwork(network_id, name, settings, given["via"])
        browser_login = (
            isinstance(settings, TailscaleSettings)
            and given["login"] == LOGIN_BROWSER
            and (self._editing is None or "auth_key" in self._saved_secrets())
        )
        self._saving = True
        self._error = ""
        self.changed.emit()

        def stored(routing: Routing) -> None:
            self._saving = False
            if browser_login:
                self._login_pending = network_id
            self._routing.adopt(routing)
            self.saved.emit()
            self._sync()

        def failed(error: BaseException) -> None:
            self._saving = False
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.put_network(network, keep), stored, failed)

    def _settings(self, given: dict[str, str]) -> tuple[MeshSettings | None, str]:
        """The kind's settings from the dialog, or why they cannot be."""
        saved = self._saved_secrets()
        if self._kind == "tailscale":
            key = given["auth_key"] if given["login"] == LOGIN_KEY else ""
            if given["login"] == LOGIN_KEY and not key and "auth_key" not in saved:
                return None, self.tr("请填写授权密钥")
            control = given["control_url"]
            if control and not control.startswith(("https://", "http://")):
                return None, self.tr("控制服务器要以 https:// 或 http:// 开头")
            return TailscaleSettings(key, control, given["hostname"]), ""
        if self._kind == "zerotier":
            if not ZEROTIER_ID.fullmatch(given["network"]):
                return None, self.tr("网络 ID 是 16 位十六进制数")
            return ZeroTierSettings(given["network"].lower(), given["identity_secret"]), ""
        peers = peers_of(given["peers"])
        if not given["network_name"]:
            return None, self.tr("请填写网络名称")
        if not given["network_secret"] and "network_secret" not in saved:
            return None, self.tr("请填写网络密钥")
        if not peers:
            return None, self.tr("请填写至少一个对等节点")
        if given["ipv4"]:
            try:
                address = ipaddress.ip_interface(given["ipv4"])
            except ValueError:
                address = None
            if address is None or address.version != 4 or "/" not in given["ipv4"]:
                return None, self.tr("本机地址写成 10.144.144.2/24 这样")
        settings = EasyTierSettings(
            given["network_name"], given["network_secret"], peers, given["ipv4"], given["hostname"]
        )
        return settings, ""

    # -- removal -----------------------------------------------------------------------------
    @Slot(str)
    def requestRemove(self, network_id: str) -> None:
        if self._network(network_id) is not None:
            self._removing = network_id
            self.changed.emit()
            self.removalRequested.emit()

    @Property(str, notify=changed)
    def removalTitle(self) -> str:
        network = self._network(self._removing)
        return self.tr("删除「{name}」？").format(name=network.name) if network else ""

    @Property(str, notify=changed)
    def removalText(self) -> str:
        return self.tr(
            "本机的登录状态和密钥一并删除，指向它的规则会拒绝连接。设备要到网络的管理页移除。"
        )

    @Slot()
    def confirmRemove(self) -> None:
        network_id, self._removing = self._removing, ""
        if not network_id:
            return
        self._notice = ""

        def failed(error: BaseException) -> None:
            self._notice = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.remove_network(network_id), self._routing.adopt, failed)
