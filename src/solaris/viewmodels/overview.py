"""Overview ViewModel: tunnel state, the connection switch, outbound controls, traffic and the
busiest connections; and the settings page's TUN and system proxy switches."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.desktop.login import open_login_settings
from solaris.host.base import HostError, ProxyGroup, TunnelHost
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.models.library import ProfileLibrary
from solaris.models.proxies import main_group, resolve
from solaris.models.settings import Settings, SettingsError, SettingsStore
from solaris.models.tunnel import TunnelSession, TunnelSnapshot
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

REFRESH_MS = 1000
GROUPS_EVERY_TICKS = 5
MODES = (
    {"value": "rule", "label": QT_TRANSLATE_NOOP("OverviewViewModel", "规则")},
    {"value": "global", "label": QT_TRANSLATE_NOOP("OverviewViewModel", "全局")},
    {"value": "direct", "label": QT_TRANSLATE_NOOP("OverviewViewModel", "直连")},
)
STATE_TITLES = {
    "running": QT_TRANSLATE_NOOP("OverviewViewModel", "已连接"),
    "starting": QT_TRANSLATE_NOOP("OverviewViewModel", "正在连接"),
    "stopping": QT_TRANSLATE_NOOP("OverviewViewModel", "正在断开"),
    "failed": QT_TRANSLATE_NOOP("OverviewViewModel", "连接失败"),
}


def _normalized(history: tuple[int, ...], scale: float) -> list[float]:
    return [value / scale for value in history]


@QmlElement
@QmlUncreatable("OverviewViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class OverviewViewModel(QObject):
    changed = Signal()
    modeOptionsChanged = Signal()  # the UI language changed; ``changed`` fires every second
    extensionSetupRequested = Signal()

    def __init__(
        self,
        host: TunnelHost,
        library: ProfileLibrary,
        runner: AsyncRunner,
        settings: SettingsStore,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
        *,
        take_over: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """``settings`` keeps the TUN and system proxy switches for the next connection.
        ``take_over`` stops another user's running tunnel and makes this user the owner
        (D5b); None where the user cannot, as on a Windows account that is no administrator."""
        super().__init__(parent)
        self._host = host
        self._take_over = take_over
        self._library = library
        self._runner = runner
        self._settings = settings
        settings.subscribe(self._settings_changed)
        self._clock = clock
        self._session = TunnelSession(host)
        self._snapshot: TunnelSnapshot | None = None
        self._groups: tuple[ProxyGroup, ...] = ()
        self._refreshing = False
        # An action stays busy after it is done until a refresh shows what it did, so the
        # controls do not go back to the state before it for a moment.
        self._busy = False
        self._settling = False  # the action is done; the next refresh ends it
        self._acted = 0  # actions done so far, so a refresh knows which one it settles
        self._refresh_again = False  # a refresh was asked for while one was on its way
        self._ticks = 0
        self._pending = ""  # "start" or "stop" while the connection switch's action runs
        self._error = ""  # the last action's failure, until the next action
        self._connection_error = ""  # the last connect's or disconnect's failure
        # User action needed by macOS, distinct from a failed connection. Kept after the
        # guide closes so it can be reopened; neither polling nor closing it retries.
        self._extension_setup = ""  # "approval" or "pending"
        self._extension_settings_opening = False
        self._extension_settings_failed = False
        self._refresh_error = ""  # the last refresh's failure, until one succeeds
        # Another user's tunnel runs (docs/architecture.md §5.4): only they can disconnect it,
        # unless this user is an administrator and takes it over.
        self._other_user = False
        self._taking_over = False
        self._switch_error = ""
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _settings_changed(self, before: Settings, after: Settings) -> None:
        if (before.tun, before.system_proxy) != (after.tun, after.system_proxy):
            self.changed.emit()  # the switches show the settings while stopped
        elif before.delay_test != after.delay_test:
            self.changed.emit()  # the node's delay is the other test's

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self.modeOptionsChanged.emit()
        self.changed.emit()

    # -- refresh -----------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        if self._refreshing:
            # The one on its way may have started before the action it would settle.
            self._refresh_again = self._refresh_again or self._settling
            return
        self._refreshing = True
        self._refresh_again = False
        include_groups = self._ticks % GROUPS_EVERY_TICKS == 0
        self._ticks += 1
        settles = self._acted if self._settling else 0
        self._runner.submit(
            self._session.refresh(include_groups=include_groups),
            lambda snapshot: self._applied(snapshot, settles),
            lambda error: self._failed(error, settles),
        )

    @Slot()
    def refreshGroups(self) -> None:
        self._ticks = 0
        self.refresh()

    def _applied(self, snapshot: TunnelSnapshot, settles: int) -> None:
        self._refreshing = False
        self._refresh_error = ""
        self._other_user = False
        self._snapshot = snapshot
        if snapshot.status.state == "running":
            self._extension_setup = ""
        if snapshot.groups is not None:
            self._groups = snapshot.groups
        self._refreshed(settles)

    def _failed(self, error: BaseException, settles: int) -> None:
        self._refreshing = False
        self._snapshot = None
        self._refresh_error = failure_text(error)
        self._other_user = isinstance(error, HostError) and error.code == "owned_by_other_user"
        self._refreshed(settles)

    def _refreshed(self, settles: int) -> None:
        """``settles`` is the action done before this refresh started; 0 for none."""
        if self._settling and settles == self._acted:
            self._settling = self._busy = self._taking_over = False
            self._pending = ""
        self.changed.emit()
        if self._refresh_again:
            self.refresh()

    def _act(
        self,
        action: Coroutine[Any, Any, None],
        done: Callable[[], None] | None = None,
        *,
        switch: bool = False,
        pending: str = "",
    ) -> None:
        """Run ``action``; ``done`` runs on the Qt thread once it succeeded. A failed
        ``switch`` also shows beside the switches on the settings page. ``pending`` is the
        connection switch's "start" or "stop": its failure shows in the switch's row."""
        self._busy = True
        self._settling = False
        self._pending = pending
        self._error = self._switch_error = self._connection_error = ""
        self.changed.emit()

        def acted(_: object) -> None:
            if done is not None:
                done()
            self._acted += 1
            self._settling = True
            self.refreshGroups()

        def failed(error: BaseException) -> None:
            self._busy = self._taking_over = False
            self._pending = ""
            if pending:
                self._extension_setup = (
                    {
                        "extension_needs_approval": "approval",
                        "extension_needs_reboot": "pending",
                    }.get(error.code, "")
                    if pending == "start" and isinstance(error, HostError)
                    else ""
                )
                self._connection_error = "" if self._extension_setup else failure_text(error)
            else:
                self._error = failure_text(error)
            if switch:
                self._switch_error = failure_text(error)
            self.changed.emit()
            if pending and self._extension_setup:
                self.extensionSetupRequested.emit()

        self._runner.submit(action, acted, failed)

    def _keep_switch(self, **setting: bool) -> None:
        """Store a switch for the next connection."""
        try:
            self._settings.update(**setting)
        except SettingsError as error:
            self._error = self._switch_error = failure_text(error)

    # -- actions -----------------------------------------------------------------------------
    @Slot(bool)
    def setConnected(self, on: bool) -> None:
        """The connection switch (D26): connect with what the settings page's switches ask
        for, as far as the host takes it over, or disconnect."""
        if self._busy or on == self._connected():
            return
        if on:
            self._act(self._library.tunnel.start(), pending="start")
        else:
            self._act(self._host.stop(), pending="stop")

    @Slot()
    def toggleConnection(self) -> None:
        """The tray's 连接 / 断开."""
        self.setConnected(not self._connected())

    @Property(str, notify=changed)
    def extensionSetup(self) -> str:
        return self._extension_setup

    @Slot()
    def showExtensionSetup(self) -> None:
        if self._extension_setup:
            self.extensionSetupRequested.emit()

    @Property(bool, notify=changed)
    def extensionSettingsOpening(self) -> bool:
        return self._extension_settings_opening

    @Property(str, notify=changed)
    def extensionSettingsError(self) -> str:
        if self._extension_settings_failed:
            return self.tr("无法打开系统设置，请按上方路径手动打开")
        return ""

    @Slot()
    def openExtensionSettings(self) -> None:
        if self._extension_setup != "approval" or self._extension_settings_opening:
            return
        self._extension_settings_opening = True
        self._extension_settings_failed = False
        self.changed.emit()

        def finished(failed: bool) -> None:
            self._extension_settings_opening = False
            self._extension_settings_failed = failed
            self.changed.emit()

        self._runner.submit(
            asyncio.to_thread(open_login_settings),
            lambda _: finished(False),
            lambda _: finished(True),
        )

    @Slot()
    def takeOver(self) -> None:
        """Stop another user's running tunnel and become the owner, once the user confirmed
        it here and in UAC (D5b). The tunnel stays down until they connect."""
        take_over = self._take_over
        if self._busy or not self._can_take_over() or take_over is None:
            return

        async def taking_over() -> None:
            try:
                await take_over()
            except HostError as error:
                if error.code != "cancelled":  # the user declined UAC's prompt
                    raise

        self._taking_over = True
        self._act(taking_over())

    @Slot(str)
    def setMode(self, mode: str) -> None:
        self._act(self._host.set_mode(mode))

    # TUN and the system proxy change the running tunnel first; the setting follows once
    # the host accepted it. While stopped only the setting changes.
    @Slot(bool)
    def setTun(self, enabled: bool) -> None:
        self._act(
            self._library.tunnel.set_tun(enabled),
            lambda: self._keep_switch(tun=enabled),
            switch=True,
        )

    @Slot(bool)
    def setSystemProxy(self, enabled: bool) -> None:
        self._act(
            self._library.tunnel.set_system_proxy(enabled),
            lambda: self._keep_switch(system_proxy=enabled),
            switch=True,
        )

    # -- status ------------------------------------------------------------------------------
    def _state(self) -> str:
        return self._snapshot.status.state if self._snapshot else ""

    def _connected(self) -> bool:
        return self._state() == "running"

    @Property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected()

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def ownedByOtherUser(self) -> bool:
        return self._other_user

    def _can_take_over(self) -> bool:
        return self._other_user and self._take_over is not None and not self._taking_over

    @Property(bool, notify=changed)
    def canTakeOver(self) -> bool:
        """An administrator may take over another user's running tunnel (D5b)."""
        return self._can_take_over()

    @Property(str, notify=changed)
    def statusTitle(self) -> str:
        # The service restarts for its new owner while it is taken over.
        if self._other_user or self._taking_over:
            return self.tr("其他用户正在使用")
        if self._extension_setup and not self._pending:
            return (
                self.tr("网络扩展尚未就绪")
                if self._extension_setup == "pending"
                else self.tr("等待授权")
            )
        title = STATE_TITLES.get(self._snapshot.status.state) if self._snapshot else None
        return self.tr(title) if title else self.tr("未连接")

    @Property(str, notify=changed)
    def statusDetail(self) -> str:
        if self._taking_over:
            return self.tr("正在接管…")
        if self._other_user and not self._error:
            if self._take_over is not None:
                return self.tr("另一位用户已连接")
            return self.tr("另一位用户已连接，对方断开后才能在这里连接")
        if self._error or self._refresh_error:
            return self._error or self._refresh_error
        if not self._connected() or self._snapshot is None:
            return ""
        started = self._snapshot.status.started_at_unix_ms / 1000
        return self.tr("{inbound} · 已运行 {uptime}").format(
            inbound=self._inbound(), uptime=fmt.uptime(int(self._clock() - started))
        )

    def _inbound(self) -> str:
        """What the running tunnel takes over; with neither, only the mixed port serves."""
        assert self._snapshot is not None
        status = self._snapshot.status
        if status.tun_enabled and status.system_proxy_enabled:
            return self.tr("TUN 与系统代理")
        if status.tun_enabled:
            return "TUN"
        if status.system_proxy_enabled:
            return self.tr("系统代理")
        return self.tr("混合端口 {port}").format(port=self._settings.current.mixed_port)

    @Property(str, notify=changed)
    def connectLabel(self) -> str:
        return self.tr("断开") if self._connected() else self.tr("连接")

    # -- the connection switch ---------------------------------------------------------------
    def _changing(self) -> str:
        """ "start" or "stop" while this switch or the host brings the tunnel up or down."""
        if self._pending:
            return self._pending
        return {"starting": "start", "stopping": "stop"}.get(self._state(), "")

    @Property(bool, notify=changed)
    def connectionOn(self) -> bool:
        """On while connected, and while connecting."""
        changing = self._changing()
        return changing == "start" if changing else self._connected()

    @Property(bool, notify=changed)
    def connectionChanging(self) -> bool:
        return self._changing() != ""

    @Property(str, notify=changed)
    def connectionDetail(self) -> str:
        """The switch's second line: what it is doing, or why it could not."""
        changing = self._changing()
        if changing == "start":
            return self.tr("正在连接…")
        if changing == "stop":
            return self.tr("正在断开…")
        if self._extension_setup == "approval":
            return self.tr("允许网络扩展后继续连接")
        if self._extension_setup == "pending":
            return self.tr("按系统提示完成设置后重试")
        return self._connection_error

    @Property(bool, notify=changed)
    def connectionFailed(self) -> bool:
        return self._changing() == "" and self._connection_error != ""

    # -- outbound ----------------------------------------------------------------------------
    @Property("QVariantList", notify=modeOptionsChanged)  # pyright: ignore[reportArgumentType]
    def modeOptions(self) -> list[dict[str, str]]:
        return [{**mode, "label": self.tr(mode["label"])} for mode in MODES]

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._snapshot.status.mode if self._snapshot else "rule"

    # -- the settings page's TUN and system proxy -------------------------------------------
    @Property(str, constant=True)
    def takeover(self) -> str:
        """TAKEOVER_SETTINGS shows both switches; the macOS extension always runs a TUN, and
        the development service takes over nothing (TunnelHost.takeover)."""
        return self._host.takeover

    # The switches show the running tunnel, or else what the next connection asks for.
    @Property(bool, notify=changed)
    def tunEnabled(self) -> bool:
        if self._connected() and self._snapshot is not None:
            return self._snapshot.status.tun_enabled
        return self._settings.current.tun

    @Property(bool, notify=changed)
    def systemProxyEnabled(self) -> bool:
        if self._connected() and self._snapshot is not None:
            return self._snapshot.status.system_proxy_enabled
        return self._settings.current.system_proxy

    @Property(str, notify=changed)
    def switchError(self) -> str:
        """Why the last change of TUN or the system proxy failed."""
        return self._switch_error

    @Property(str, notify=changed)
    def nodeGroup(self) -> str:
        group = main_group(list(self._groups))
        return group.name if group else self.tr("节点")

    @Property(str, notify=changed)
    def nodeName(self) -> str:
        group = main_group(list(self._groups))
        proxy = resolve(list(self._groups), group.name) if group else None
        return proxy.name if proxy else "—"

    def _node_latency(self) -> tuple[str, str]:
        group = main_group(list(self._groups))
        proxy = resolve(list(self._groups), group.name) if group else None
        delay = proxy.delay(self._settings.current.delay_test) if proxy else None
        if proxy is None or proxy.type in ("Direct", "Reject") or delay is None:
            return "", "none"
        return fmt.latency(delay)

    @Property(str, notify=changed)
    def nodeDelay(self) -> str:
        return self._node_latency()[0]

    @Property(str, notify=changed)
    def nodeDelayLevel(self) -> str:
        return self._node_latency()[1]

    # -- traffic -----------------------------------------------------------------------------
    def _latest(self, pick: Callable[[Any], int], formatter: Callable[[int], str]) -> str:
        latest = self._snapshot.latest if self._snapshot else None
        return formatter(pick(latest)) if latest else "—"

    @Property(str, notify=changed)
    def downRate(self) -> str:
        return self._latest(lambda s: s.down_bytes_per_second, fmt.rate)

    @Property(str, notify=changed)
    def upRate(self) -> str:
        return self._latest(lambda s: s.up_bytes_per_second, fmt.rate)

    @Property(str, notify=changed)
    def downTotal(self) -> str:
        return self._latest(lambda s: s.down_total_bytes, fmt.size)

    @Property(str, notify=changed)
    def upTotal(self) -> str:
        return self._latest(lambda s: s.up_total_bytes, fmt.size)

    def _scale(self) -> float:
        if self._snapshot is None:
            return 1.0
        return float(max(*self._snapshot.down_history, *self._snapshot.up_history, 1))

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def downPoints(self) -> list[float]:
        return _normalized(self._snapshot.down_history, self._scale()) if self._snapshot else []

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def upPoints(self) -> list[float]:
        return _normalized(self._snapshot.up_history, self._scale()) if self._snapshot else []

    # -- connections -------------------------------------------------------------------------
    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def recent(self) -> list[dict[str, str]]:
        if self._snapshot is None:
            return []
        return [
            {"host": r.host, "route": r.route, "rate": fmt.rate(r.download_bytes_per_second)}
            for r in self._snapshot.recent
        ]

    @Property(str, notify=changed)
    def connectionsLink(self) -> str:
        if not self._connected() or self._snapshot is None:
            return self.tr("全部")
        return self.tr("全部 %n 个", "", self._snapshot.status.connection_count)
