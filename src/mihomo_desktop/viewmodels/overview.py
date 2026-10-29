"""Overview ViewModel: tunnel state, outbound controls, traffic and the busiest connections."""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import ProxyGroup, RunOptions, TunnelHost
from mihomo_desktop.models import format as fmt
from mihomo_desktop.models.library import ProfileLibrary
from mihomo_desktop.models.proxies import main_group, resolve
from mihomo_desktop.models.tunnel import TunnelSession, TunnelSnapshot
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

REFRESH_MS = 1000
GROUPS_EVERY_TICKS = 5
MODES = (
    {"value": "rule", "label": "规则"},
    {"value": "global", "label": "全局"},
    {"value": "direct", "label": "直连"},
)
STATE_TITLES = {
    "running": "已连接",
    "starting": "正在连接",
    "stopping": "正在断开",
    "failed": "连接失败",
}


def _normalized(history: tuple[int, ...], scale: float) -> list[float]:
    return [value / scale for value in history]


@QmlElement
@QmlUncreatable("OverviewViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class OverviewViewModel(QObject):
    changed = Signal()

    def __init__(
        self,
        host: TunnelHost,
        library: ProfileLibrary,
        runner: AsyncRunner,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._library = library
        self._runner = runner
        self._clock = clock
        self._session = TunnelSession(host)
        self._snapshot: TunnelSnapshot | None = None
        self._groups: tuple[ProxyGroup, ...] = ()
        self._refreshing = False
        self._busy = False
        self._ticks = 0
        self._error = ""
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # -- refresh -----------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        include_groups = self._ticks % GROUPS_EVERY_TICKS == 0
        self._ticks += 1
        self._runner.submit(
            self._session.refresh(include_groups=include_groups), self._applied, self._failed
        )

    @Slot()
    def refreshGroups(self) -> None:
        self._ticks = 0
        self.refresh()

    def _applied(self, snapshot: TunnelSnapshot) -> None:
        self._refreshing = False
        self._snapshot = snapshot
        if snapshot.groups is not None:
            self._groups = snapshot.groups
        self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self._refreshing = False
        self._snapshot = None
        self._error = failure_text(error)
        self.changed.emit()

    def _act(self, action: Coroutine[Any, Any, None]) -> None:
        self._busy = True
        self._error = ""
        self.changed.emit()
        self._runner.submit(action, self._acted, self._action_failed)

    def _acted(self, _: object) -> None:
        self._busy = False
        self.refreshGroups()

    def _action_failed(self, error: BaseException) -> None:
        self._busy = False
        self._error = failure_text(error)
        self.changed.emit()

    # -- actions -----------------------------------------------------------------------------
    @Slot()
    def toggleConnection(self) -> None:
        if self._connected():
            self._act(self._host.stop())
            return
        # Connect with what the switches show; the settings page (roadmap M3) will own
        # the rest of RunOptions.
        status = self._snapshot.status if self._snapshot else None
        options = RunOptions(
            tun_enabled=bool(status and status.tun_enabled),
            system_proxy_enabled=bool(status and status.system_proxy_enabled),
        )
        self._act(self._library.start_tunnel(options))

    @Slot(str)
    def setMode(self, mode: str) -> None:
        self._act(self._host.set_mode(mode))

    @Slot(bool)
    def setTun(self, enabled: bool) -> None:
        self._act(self._host.set_tun(enabled))

    @Slot(bool)
    def setSystemProxy(self, enabled: bool) -> None:
        self._act(self._host.set_system_proxy(enabled))

    # -- status ------------------------------------------------------------------------------
    def _connected(self) -> bool:
        return self._snapshot is not None and self._snapshot.status.state == "running"

    @Property(bool, notify=changed)
    def connected(self) -> bool:
        return self._connected()

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def statusTitle(self) -> str:
        if self._snapshot is None:
            return "未连接"
        return STATE_TITLES.get(self._snapshot.status.state, "未连接")

    @Property(str, notify=changed)
    def statusDetail(self) -> str:
        if self._error:
            return self._error
        if not self._connected() or self._snapshot is None:
            return "隧道未运行"
        started = self._snapshot.status.started_at_unix_ms / 1000
        return "已运行 " + fmt.uptime(int(self._clock() - started))

    @Property(str, notify=changed)
    def connectLabel(self) -> str:
        return "断开" if self._connected() else "连接"

    # -- outbound ----------------------------------------------------------------------------
    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def modeOptions(self) -> list[dict[str, str]]:
        return list(MODES)

    @Property(str, notify=changed)
    def mode(self) -> str:
        return self._snapshot.status.mode if self._snapshot else "rule"

    @Property(bool, notify=changed)
    def tunEnabled(self) -> bool:
        return self._snapshot.status.tun_enabled if self._snapshot else False

    @Property(bool, notify=changed)
    def systemProxyEnabled(self) -> bool:
        return self._snapshot.status.system_proxy_enabled if self._snapshot else False

    @Property(str, notify=changed)
    def nodeGroup(self) -> str:
        group = main_group(list(self._groups))
        return group.name if group else "节点"

    @Property(str, notify=changed)
    def nodeName(self) -> str:
        group = main_group(list(self._groups))
        proxy = resolve(list(self._groups), group.name) if group else None
        return proxy.name if proxy else "—"

    def _node_latency(self) -> tuple[str, str]:
        group = main_group(list(self._groups))
        proxy = resolve(list(self._groups), group.name) if group else None
        if proxy is None or proxy.type in ("Direct", "Reject"):
            return "", "none"
        return fmt.latency(proxy.delay_ms)

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
            return "全部"
        return f"全部 {self._snapshot.status.connection_count} 个"
