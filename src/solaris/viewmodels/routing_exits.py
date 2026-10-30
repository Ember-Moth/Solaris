"""The chained exits on the routing page's groups tab (docs/exits.md §2, §6): the WARP and
VPN Gate exits, their rows and their settings dialogs.

Each control of the dialog applies at once, as a switch does (DESIGN.md "开关"): turning the
exit on and its via group change the plan, which is validated, stored and reloaded like any
other change; the protocol is a setting of this machine. The core registers the account
through the via group of the running plan (§3.2), so a registration asked for while the
tunnel is stopped waits for the next connection. So does a key for a new protocol.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import WARP_PROTOCOLS, HostError, VpnGateServer, WarpAccount
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.models import plans
from solaris.models.library import ProfileLibrary, Routing
from solaris.models.profiles import ProfileError
from solaris.models.regions import region_name
from solaris.models.settings import SettingsStore
from solaris.models.vpngate import ServerList
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text
from solaris.viewmodels.routing_groups import ChoiceRow

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

PROTOCOLS = {
    "masque-h2": (
        QT_TRANSLATE_NOOP("WarpExit", "MASQUE（HTTP/2）"),
        QT_TRANSLATE_NOOP("WarpExit", "只需要 TCP，任何节点都能当跳板"),
    ),
    "masque-h3": (
        QT_TRANSLATE_NOOP("WarpExit", "MASQUE（HTTP/3）"),
        QT_TRANSLATE_NOOP("WarpExit", "节点要能转发 UDP；开销更小"),
    ),
    "wireguard": (
        QT_TRANSLATE_NOOP("WarpExit", "WireGuard"),
        QT_TRANSLATE_NOOP("WarpExit", "节点要能转发 UDP；开销最小"),
    ),
}
assert tuple(PROTOCOLS) == WARP_PROTOCOLS
# The row's state and how it reads: "" as text, "muted" for the quiet ones, "warning" when
# the exit's rules reject their connections.
TONE_NORMAL, TONE_MUTED, TONE_WARNING = "", "muted", "warning"


@QmlElement
@QmlUncreatable("WarpExit is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class WarpExit(QObject):
    changed = Signal()
    opened = Signal()  # the view opens the dialog
    removalRequested = Signal()  # deleting the account awaits confirmation

    def __init__(
        self,
        routing: RoutingViewModel,
        runner: AsyncRunner,
        library: ProfileLibrary,
        settings: SettingsStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._routing = routing
        self._runner = runner
        self._library = library
        self._settings = settings
        self._running = False
        self._registering = False
        # A registration asked for while stopped: whether it is for a new device; None when
        # none waits.
        self._pending: bool | None = None
        self._error = ""  # why the last registration or deletion failed
        self._change_error = ""  # why the plan or the setting did not take a change
        routing.changed.connect(self.changed)

    def retranslate(self) -> None:
        self.changed.emit()

    def _account(self) -> WarpAccount | None:
        return self._routing.warp_account()

    def _exit(self) -> plans.WarpExit:
        return plans.warp_exit(self._routing.plan())

    def _protocol(self) -> str:
        return self._settings.current.warp_protocol

    # -- the tunnel --------------------------------------------------------------------------
    def set_running(self, running: bool) -> None:
        """The tunnel started or stopped. Once it runs, what waited for it goes ahead: the
        registration asked for, or a key for the protocol chosen meanwhile."""
        if running == self._running:
            return
        self._running = running
        if running and not self._registering and self._routing.routes_with_plan():
            account = self._account()
            if self._pending is not None:
                self._register(renew=self._pending and account is not None)
            elif account is not None and account.protocol != self._protocol():
                self._register(renew=False)
        self.changed.emit()

    # -- the row -----------------------------------------------------------------------------
    @Property(str, notify=changed)
    def name(self) -> str:
        return text.target_label(plans.WARP, None)

    def _via_label(self) -> str:
        return text.target_label(self._exit().via, self._routing.plan(), self._routing.report())

    def _via_empty(self) -> bool:
        report = self._routing.report()
        return report is not None and any(
            w.entry_id == plans.WARP and w.reason == "via_empty" for w in report.warnings
        )

    @Property(str, notify=changed)
    def caption(self) -> str:
        via = self._via_label()
        if self._via_empty():
            via = self.tr("{via}，暂经节点选择").format(via=via)
        protocol = self.tr(PROTOCOLS[self._protocol()][0])
        return self.tr("经由 {via} · {protocol}").format(via=via, protocol=protocol)

    def _state(self) -> tuple[str, str]:
        if not self._exit().enabled:
            return self.tr("未启用"), TONE_MUTED
        if self._registering:
            return self.tr("正在注册…"), TONE_MUTED
        if self._account() is not None:
            return self.tr("已就绪"), TONE_NORMAL
        if self._error:
            return self.tr("不可用"), TONE_WARNING
        if self._pending is not None and not self._running:
            return self.tr("连接后注册"), TONE_MUTED
        return self.tr("未注册"), TONE_WARNING

    @Property(str, notify=changed)
    def status(self) -> str:
        return self._state()[0]

    @Property(str, notify=changed)
    def statusTone(self) -> str:
        return self._state()[1]

    # -- the dialog --------------------------------------------------------------------------
    @Slot()
    def open(self) -> None:
        self._change_error = ""
        self.changed.emit()
        self.opened.emit()

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._registering or bool(self._routing.property("busy"))

    @Property(bool, notify=changed)
    def active(self) -> bool:
        """The plan turned the exit on; ``enabled`` is an Item's own property in QML."""
        return self._exit().enabled

    @Slot(bool)
    def setActive(self, on: bool) -> None:
        """Turn the exit on in the plan, and register when there is no account yet."""
        warp = self._exit()
        if on == warp.enabled:
            return

        def stored() -> None:
            if not on:
                self._pending = None
            elif self._account() is None and not self._registering:
                self._register(renew=False)

        self._put(replace(warp, enabled=on), stored)

    @Property(str, notify=changed)
    def via(self) -> str:
        return self._exit().via

    @Property(str, notify=changed)
    def viaLabel(self) -> str:
        return self._via_label()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def viaOptions(self) -> list[dict[str, object]]:
        return text.group_options(self._routing.plan(), self._routing.report())

    @Slot(str)
    def setVia(self, group_id: str) -> None:
        warp = self._exit()
        if group_id != warp.via:
            self._put(replace(warp, via=group_id))

    def _put(self, warp: plans.WarpExit, stored: Callable[[], None] | None = None) -> None:
        plan = self._routing.plan()
        if plan is None:
            return
        self._change_error = ""

        def done() -> None:
            if stored is not None:
                stored()
            self.changed.emit()

        def failed(message: str) -> None:
            self._change_error = message

        self._routing.put_plan(plans.with_warp_exit(plan, warp), done, failed)

    @Property(str, notify=changed)
    def protocol(self) -> str:
        return self._protocol()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def protocolOptions(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": self.tr(label), "detail": self.tr(detail)}
            for value, (label, detail) in PROTOCOLS.items()
        ]

    @Slot(str)
    def setProtocol(self, protocol: str) -> None:
        """Choose the protocol of this machine; an account gets a key for it now, or once
        the tunnel runs."""
        if protocol == self._protocol() or protocol not in PROTOCOLS:
            return
        self._change_error = ""
        try:
            self._settings.update(warp_protocol=protocol)
        except (ProfileError, ValueError) as error:
            self._change_error = failure_text(error)
            self.changed.emit()
            return
        account = self._account()
        if account is not None and account.protocol != protocol and not self._registering:
            self._register(renew=False)
        self.changed.emit()

    @Property(str, notify=changed)
    def changeError(self) -> str:
        return self._change_error

    # -- the account -------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def hasAccount(self) -> bool:
        return self._account() is not None

    @Property(str, notify=changed)
    def accountTitle(self) -> str:
        if self._registering:
            return self.tr("正在注册…")
        return self.tr("已注册") if self._account() is not None else self.tr("未注册")

    @Property(str, notify=changed)
    def accountDetail(self) -> str:
        """When and which device, or what waits; a failure is ``error``."""
        account = self._account()
        if self._registering:
            return self.tr("经节点连接 Cloudflare，它看不到你的 IP")
        if account is None:
            if self._pending is not None and not self._running:
                return self.tr("连接后经节点注册")
            return self.tr("经节点注册，Cloudflare 看不到你的 IP")
        registered = self.tr("{date} · 设备 {device}").format(
            date=fmt.date(account.registered_at_unix_ms / 1000), device=account.device_id[:8]
        )
        if account.protocol != self._protocol():
            protocol = self.tr(PROTOCOLS[self._protocol()][0])
            waiting = self.tr("连接后换用 {protocol}").format(protocol=protocol)
            return f"{registered} · {waiting}"
        return registered

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def registerLabel(self) -> str:
        return self.tr("重新注册") if self._account() is not None else self.tr("注册")

    @Property(bool, notify=changed)
    def canRegister(self) -> bool:
        return not self._registering and self._routing.plan() is not None

    @Slot()
    def register(self) -> None:
        """Register this device, or a new device in place of the one it has."""
        if self.canRegister:
            self._register(renew=self._account() is not None)

    def _register(self, *, renew: bool) -> None:
        self._error = ""
        if not self._running or not self._routing.routes_with_plan():
            self._pending = renew
            self.changed.emit()
            return
        self._pending = None
        self._registering = True
        self.changed.emit()

        def registered(routing: Routing) -> None:
            self._registering = False
            self._routing.adopt(routing)

        def failed(error: BaseException) -> None:
            self._registering = False
            if isinstance(error, HostError) and error.code == "not_running":
                self._running, self._pending = False, renew
            else:
                self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.register_warp(renew=renew), registered, failed)

    @Slot()
    def requestForget(self) -> None:
        if self._account() is not None:
            self.removalRequested.emit()

    @Slot()
    def confirmForget(self) -> None:
        """Delete this machine's copy of the account; the exit's rules reject from now on."""
        self._error = ""
        self._pending = None

        def failed(error: BaseException) -> None:
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.forget_warp(), self._routing.adopt, failed)


# -- the VPN Gate exit (docs/exits.md §5) ------------------------------------------------------
MODES = {
    "per-site": (
        QT_TRANSLATE_NOOP("VpnGateExit", "按网站分配"),
        QT_TRANSLATE_NOOP("VpnGateExit", "同一网站总走同一台，网站看到的 IP 不变"),
    ),
    "single": (
        QT_TRANSLATE_NOOP("VpnGateExit", "只用一台"),
        QT_TRANSLATE_NOOP("VpnGateExit", "所有网站走同一台，失效后换下一台"),
    ),
}
assert tuple(MODES) == plans.VPNGATE_MODES
# The servers are checked as often as the exit's group tests them; the list is fetched again
# once it is an hour old (vpngate.REFRESH_S).
CHECK_MS = 120_000


@dataclass(frozen=True)
class ServerRow:
    """A server the exit uses, in the order of its group."""

    id: str  # the host name
    title: str  # 日本 · vpn381472650
    detail: str  # the volunteer's name
    delay: str
    delayLevel: str  # fmt.latency's level


@QmlElement
@QmlUncreatable("VpnGateExit is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class VpnGateExit(QObject):
    """The VPN Gate exit's row and settings dialog. The plan holds its switch, via group,
    countries, server count and how sites are shared out; each applies at once. The core
    fetches the list through the via group of the running plan, so a refresh asked for while
    the tunnel is stopped waits for the next connection; while it runs, the list is fetched
    again hourly and the servers are checked every two minutes (§5.2)."""

    changed = Signal()
    opened = Signal()

    def __init__(
        self,
        routing: RoutingViewModel,
        runner: AsyncRunner,
        library: ProfileLibrary,
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(parent)
        self._routing = routing
        self._runner = runner
        self._library = library
        self._clock = clock
        self._running = False
        self._refreshing = False
        self._checking = False
        self._pending = False  # a refresh asked for while stopped
        self._error = ""  # why the last refresh failed
        self._change_error = ""
        roles = ["itemId" if n == "id" else n for n in ChoiceRow.__dataclass_fields__]
        self._countries = KeyedListModel(roles, self)
        self._servers = KeyedListModel(list(ServerRow.__dataclass_fields__), self)
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self._tick)
        routing.changed.connect(self.changed)
        self.changed.connect(self._sync_rows)
        self._sync_rows()

    def retranslate(self) -> None:
        self.changed.emit()

    def _exit(self) -> plans.VpnGateExit:
        return plans.vpngate_exit(self._routing.plan())

    def _list(self) -> ServerList:
        return self._routing.vpngate_list()

    def _usable(self) -> bool:
        """The core can fetch the list: the tunnel runs the plan, which turned the exit on."""
        return self._running and self._routing.routes_with_plan() and self._exit().enabled

    # -- the tunnel and the timer ------------------------------------------------------------
    def set_running(self, running: bool) -> None:
        """The tunnel started or stopped. Once it runs, a refresh that waited goes ahead, and
        so does one for a list over an hour old."""
        if running == self._running:
            return
        self._running = running
        if running:
            self._timer.start()
            if self._pending or self._list().stale(self._clock()):
                self._refresh()
        else:
            self._timer.stop()
        self.changed.emit()

    def _tick(self) -> None:
        if not self._usable() or self._refreshing or self._checking:
            return
        if self._list().stale(self._clock()):
            self._refresh()
            return
        if not self._list().picked:
            return
        self._checking = True

        def done(routing: Routing) -> None:
            self._checking = False
            self._routing.adopt(routing)

        def failed(error: BaseException) -> None:
            self._checking = False
            if not isinstance(error, HostError | ProfileError):
                raise error

        self._runner.submit(self._library.check_vpngate(), done, failed)

    # -- the row -----------------------------------------------------------------------------
    @Property(str, notify=changed)
    def name(self) -> str:
        return text.target_label(plans.VPNGATE, None)

    def _via_label(self) -> str:
        return text.target_label(self._exit().via, self._routing.plan(), self._routing.report())

    def _via_empty(self) -> bool:
        report = self._routing.report()
        return report is not None and any(
            w.entry_id == plans.VPNGATE and w.reason == "via_empty" for w in report.warnings
        )

    def _country_names(self, codes: Iterable[str]) -> str:
        return self.tr("、").join(region_name(code) for code in codes)

    @Property(str, notify=changed)
    def caption(self) -> str:
        via = self._via_label()
        if self._via_empty():
            via = self.tr("{via}，暂经节点选择").format(via=via)
        countries = self._country_names(self._exit().countries) or self.tr("未选国家")
        return self.tr("经由 {via} · {countries}").format(via=via, countries=countries)

    def _picked(self) -> tuple[VpnGateServer, ...]:
        return self._list().picked_servers() if self._exit().enabled else ()

    def _state(self) -> tuple[str, str]:
        if not self._exit().enabled:
            return self.tr("未启用"), TONE_MUTED
        if self._refreshing:
            return self.tr("正在获取…"), TONE_MUTED
        picked = self._picked()
        if picked:
            countries = {s.country for s in picked}
            count = self.tr("%n 台", "", len(picked))
            if len(countries) == 1:
                return f"{region_name(next(iter(countries)))} {count}", TONE_NORMAL
            return count, TONE_NORMAL
        if not self._list().servers and not self._running and not self._error:
            return self.tr("连接后获取"), TONE_MUTED
        return self.tr("不可用"), TONE_WARNING

    @Property(str, notify=changed)
    def status(self) -> str:
        return self._state()[0]

    @Property(str, notify=changed)
    def statusTone(self) -> str:
        return self._state()[1]

    # -- the dialog --------------------------------------------------------------------------
    @Slot()
    def open(self) -> None:
        self._change_error = ""
        self.changed.emit()
        self.opened.emit()

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return bool(self._routing.property("busy"))

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return self._exit().enabled

    @Slot(bool)
    def setActive(self, on: bool) -> None:
        """Turn the exit on in the plan, and fetch the list when there is none fresh."""
        vpngate = self._exit()
        if on == vpngate.enabled:
            return

        def stored() -> None:
            if not on:
                self._pending = False
            elif self._list().stale(self._clock()) and not self._refreshing:
                self._refresh()

        self._put(replace(vpngate, enabled=on), stored)

    @Property(str, notify=changed)
    def via(self) -> str:
        return self._exit().via

    @Property(str, notify=changed)
    def viaLabel(self) -> str:
        return self._via_label()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def viaOptions(self) -> list[dict[str, object]]:
        return text.group_options(self._routing.plan(), self._routing.report())

    @Slot(str)
    def setVia(self, group_id: str) -> None:
        vpngate = self._exit()
        if group_id != vpngate.via:
            self._put(replace(vpngate, via=group_id))

    @Property(QObject, constant=True)
    def countries(self) -> KeyedListModel:
        """The countries of the list with their server counts, and those the plan chose."""
        return self._countries

    @Property(bool, notify=changed)
    def hasCountries(self) -> bool:
        return self._countries.rowCount() > 0

    @Slot(str)
    def toggleCountry(self, code: str) -> None:
        vpngate = self._exit()
        chosen = list(vpngate.countries)
        if code in chosen:
            if len(chosen) == 1:
                return  # one country at least, or the exit has nothing to pick
            chosen.remove(code)
        else:
            chosen.append(code)
        self._put(replace(vpngate, countries=tuple(chosen)))

    @Property(bool, notify=changed)
    def severalCountries(self) -> bool:
        return len(self._exit().countries) > 1

    @Property(str, notify=changed)
    def count(self) -> str:
        return str(self._exit().count)

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def countOptions(self) -> list[dict[str, str]]:
        return [
            {"value": str(n), "label": self.tr("%n 台", "", n)}
            for n in range(1, plans.VPNGATE_MAX_COUNT + 1)
        ]

    @Property(str, notify=changed)
    def countLabel(self) -> str:
        return self.tr("%n 台", "", self._exit().count)

    @Slot(str)
    def setCount(self, value: str) -> None:
        vpngate = self._exit()
        count = int(value)
        if count != vpngate.count and 1 <= count <= plans.VPNGATE_MAX_COUNT:
            self._put(replace(vpngate, count=count))

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._exit().mode

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def modeOptions(self) -> list[dict[str, str]]:
        return [
            {"value": value, "label": self.tr(label), "detail": self.tr(detail)}
            for value, (label, detail) in MODES.items()
        ]

    @Slot(str)
    def setMode(self, mode: str) -> None:
        vpngate = self._exit()
        if mode != vpngate.mode and mode in MODES:
            self._put(replace(vpngate, mode=mode))

    def _put(self, vpngate: plans.VpnGateExit, stored: Callable[[], None] | None = None) -> None:
        plan = self._routing.plan()
        if plan is None:
            return
        self._change_error = ""

        def done() -> None:
            if stored is not None:
                stored()
            self.changed.emit()

        def failed(message: str) -> None:
            self._change_error = message

        self._routing.put_plan(plans.with_vpngate_exit(plan, vpngate), done, failed)

    @Property(str, notify=changed)
    def changeError(self) -> str:
        return self._change_error

    # -- the servers -------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def servers(self) -> KeyedListModel:
        """The servers the exit uses, in the order of its group."""
        return self._servers

    @Property(str, notify=changed)
    def serversTitle(self) -> str:
        if self._refreshing:
            return self.tr("正在获取服务器列表…")
        picked = self._picked()
        if picked:
            return self.tr("在用 %n 台服务器", "", len(picked))
        return self.tr("没有在用的服务器")

    @Property(str, notify=changed)
    def serversDetail(self) -> str:
        """When the list was fetched, or why there are no servers; a failure is ``error``."""
        servers = self._list()
        if self._refreshing:
            return self.tr("经节点连接 VPN Gate")
        if not servers.servers:
            if self._running and self._routing.routes_with_plan():
                return self.tr("还没有获取服务器列表")
            return self.tr("连接后经节点获取服务器列表")
        fetched = self.tr("列表更新于 {ago}").format(ago=fmt.ago(servers.fetched_at, self._clock()))
        if self._exit().enabled and not self._picked():
            return self.tr("所选国家暂无可用的服务器") + " · " + fetched
        return fetched

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(bool, notify=changed)
    def canRefresh(self) -> bool:
        return not self._refreshing and self._routing.plan() is not None

    @Slot()
    def refresh(self) -> None:
        """Fetch the list now, or once the tunnel runs."""
        if self.canRefresh:
            self._refresh()

    def _refresh(self) -> None:
        self._error = ""
        if not self._running or not self._routing.routes_with_plan():
            self._pending = True
            self.changed.emit()
            return
        self._pending = False
        self._refreshing = True
        self.changed.emit()

        def refreshed(routing: Routing) -> None:
            self._refreshing = False
            self._routing.adopt(routing)

        def failed(error: BaseException) -> None:
            self._refreshing = False
            if isinstance(error, HostError) and error.code == "not_running":
                self._running, self._pending = False, True
            else:
                self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.refresh_vpngate(), refreshed, failed)

    def _sync_rows(self) -> None:
        servers = self._list()
        chosen = self._exit().countries
        counts = dict(servers.countries())
        codes = [code for code, _ in servers.countries()]
        codes += [code for code in chosen if code not in counts]
        self._countries.sync(
            [
                ChoiceRow(
                    code,
                    region_name(code),
                    str(counts[code]) if code in counts else "",
                    code in chosen,
                    False,
                )
                for code in codes
            ]
        )
        rows = []
        for server in self._picked():
            delay, level = fmt.latency(servers.delays.get(server.host_name))
            title = f"{region_name(server.country)} · {server.host_name}"
            rows.append(ServerRow(server.host_name, title, server.operator, delay, level))
        self._servers.sync(rows)
