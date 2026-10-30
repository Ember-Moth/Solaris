"""Connections ViewModel: the live table, its filters and sort, and closing connections."""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import TunnelHost
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.models.connections import (
    NETWORKS,
    SORT_KEYS,
    ConnectionMonitor,
    ConnectionsState,
    TrackedConnection,
    address,
    arrange,
    chain,
    process_name,
    protocol,
)
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

REFRESH_MS = 1000
NETWORK_LABELS = {
    "all": QT_TRANSLATE_NOOP("ConnectionsViewModel", "全部"),
    "tcp": "TCP",
    "udp": "UDP",
}


@dataclass(frozen=True)
class ConnectionRow:
    """One table row, as text."""

    id: str
    host: str
    protocol: str
    ruleName: str  # the plan's rule entry (routing.md §4.6); empty when none
    rule: str
    chain: str
    process: str
    downRate: str
    upRate: str
    download: str
    upload: str
    duration: str

    @property
    def connectionId(self) -> str:  # QML reserves ``id``
        return self.id


ROLES = (*(f.name for f in fields(ConnectionRow) if f.name != "id"), "connectionId")


def _row(tracked: TrackedConnection, now: float, entries: dict[str, str]) -> ConnectionRow:
    connection = tracked.connection
    return ConnectionRow(
        id=connection.id,
        host=address(connection),
        protocol=protocol(connection),
        ruleName=entries.get(connection.rule_entry_id, "") if connection.rule_entry_id else "",
        rule=connection.rule,
        chain=chain(connection),
        process=process_name(connection),
        downRate=fmt.rate(tracked.download_rate),
        upRate=fmt.rate(tracked.upload_rate),
        download=fmt.size(connection.download_bytes),
        upload=fmt.size(connection.upload_bytes),
        duration=fmt.uptime(int(now - connection.started_at_unix_ms / 1000)),
    )


@QmlElement
@QmlUncreatable("ConnectionsViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ConnectionsViewModel(QObject):
    changed = Signal()
    filtersChanged = Signal()
    selectionChanged = Signal()
    optionsChanged = Signal()  # the UI language changed

    def __init__(
        self,
        host: TunnelHost,
        runner: AsyncRunner,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._runner = runner
        self._clock = clock
        self._monitor = ConnectionMonitor(host)
        self._model = KeyedListModel(ROLES, self)
        self._state: ConnectionsState | None = None
        self._shown: list[TrackedConnection] = []
        self._query = ""
        self._entries: dict[str, str] = {}  # rule entry id -> its name in the plan in use
        self._network = "all"
        self._sort = "duration"
        self._descending = True
        self._selected = ""  # the id of the selected connection
        self._detail: TrackedConnection | None = None  # last seen, kept after it closes
        self._detail_alive = False
        self._error = ""
        self._refreshing = False
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    # -- polling -----------------------------------------------------------------------------
    def setActive(self, active: bool) -> None:
        """Poll only while the page shows."""
        if active and not self._timer.isActive():
            self.refresh()
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()
            self._monitor.forget()

    def stop(self) -> None:
        self._timer.stop()

    @Slot()
    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self._runner.submit(self._monitor.refresh(), self._applied, self._failed)

    def _applied(self, state: ConnectionsState) -> None:
        self._refreshing = False
        self._state = state
        self._error = ""
        self._rebuild()

    def _failed(self, error: BaseException) -> None:
        self._refreshing = False
        self._state = None
        self._error = failure_text(error)
        self._rebuild()

    def _rebuild(self) -> None:
        connections = self._state.connections if self._state else ()
        self._shown = arrange(connections, self._query, self._network, self._sort, self._descending)
        now = self._clock()
        self._model.sync([_row(c, now, self._entries) for c in self._shown])
        if self._selected:
            current = next((c for c in connections if c.id == self._selected), None)
            self._detail = current or self._detail
            self._detail_alive = current is not None
        self.changed.emit()
        self.selectionChanged.emit()

    # -- actions -----------------------------------------------------------------------------
    def _act(self, action: Coroutine[Any, Any, None]) -> None:
        self._runner.submit(action, lambda _: self.refresh(), self._action_failed)

    def _action_failed(self, error: BaseException) -> None:
        self._error = failure_text(error)
        self.changed.emit()

    @Slot(str)
    def closeConnection(self, connection_id: str) -> None:
        self._act(self._host.close_connection(connection_id))

    @Slot()
    def closeAll(self) -> None:
        self._act(self._host.close_all_connections())

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self.optionsChanged.emit()
        self.changed.emit()
        self.selectionChanged.emit()

    # -- filters -----------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def model(self) -> KeyedListModel:
        return self._model

    def set_entry_names(self, names: dict[str, str]) -> None:
        """Name the rules by the entries of the plan in use; the next refresh shows them."""
        self._entries = names

    @Property(str, notify=filtersChanged)
    def query(self) -> str:
        return self._query

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self.filtersChanged.emit()
            self._rebuild()

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def networkOptions(self) -> list[dict[str, str]]:
        return [{"value": n, "label": self.tr(NETWORK_LABELS[n])} for n in NETWORKS]

    @Property(str, notify=filtersChanged)
    def network(self) -> str:
        return self._network

    @Slot(str)
    def setNetwork(self, network: str) -> None:
        if network in NETWORKS and network != self._network:
            self._network = network
            self.filtersChanged.emit()
            self._rebuild()

    @Property(str, notify=filtersChanged)
    def sortKey(self) -> str:
        return self._sort

    @Property(bool, notify=filtersChanged)
    def sortDescending(self) -> bool:
        return self._descending

    @Slot(str)
    def sortBy(self, key: str) -> None:
        """Sort by ``key``, largest first; picking the current key again flips the order."""
        if key not in SORT_KEYS:
            return
        self._descending = not self._descending if key == self._sort else True
        self._sort = key
        self.filtersChanged.emit()
        self._rebuild()

    # -- status ------------------------------------------------------------------------------
    def _total(self) -> int:
        return len(self._state.connections) if self._state else 0

    @Property(bool, notify=changed)
    def running(self) -> bool:
        return self._state is not None and self._state.running

    @Property(int, notify=changed)
    def total(self) -> int:
        return self._total()

    @Property(bool, notify=changed)
    def hasError(self) -> bool:
        return bool(self._error)

    @Property(str, notify=changed)
    def summary(self) -> str:
        if self._error:
            return self._error
        if self._state is None:
            return ""
        if not self._state.running:
            return self.tr("未连接")
        total = self._total()
        if self._query.strip() or self._network != "all":
            return self.tr("{shown} / %n 个", "", total).format(shown=len(self._shown))
        return self.tr("%n 个", "", total)

    @Property(str, notify=changed)
    def emptyText(self) -> str:
        if self._shown or self._state is None:
            return ""
        if not self._state.running:
            return self.tr("未连接")
        if not self._state.connections:
            return self.tr("没有连接")
        return self.tr("没有匹配的连接")

    # -- selection and detail ----------------------------------------------------------------
    @Property(int, notify=selectionChanged)
    def selectedIndex(self) -> int:
        return next((n for n, c in enumerate(self._shown) if c.id == self._selected), -1)

    @Slot(str)
    def select(self, connection_id: str) -> None:
        self._selected = connection_id
        self._detail = next((c for c in self._shown if c.id == connection_id), None)
        self._detail_alive = self._detail is not None
        self.selectionChanged.emit()

    @Slot(int)
    def selectIndex(self, index: int) -> None:
        if self._shown:
            self.select(self._shown[min(max(index, 0), len(self._shown) - 1)].id)

    @Property(bool, notify=selectionChanged)
    def detailClosed(self) -> bool:
        """Whether the connection the detail shows has ended since it was selected."""
        return self._detail is not None and not self._detail_alive

    @Property("QVariantMap", notify=selectionChanged)  # pyright: ignore[reportArgumentType]
    def detail(self) -> dict[str, str]:
        if self._detail is None:
            return {}
        tracked, connection = self._detail, self._detail.connection
        started = connection.started_at_unix_ms / 1000
        return {
            "id": connection.id,
            "host": address(connection),
            "protocol": protocol(connection),
            "source": connection.source or "—",
            "destination": connection.destination or "—",
            "process": connection.process or "—",
            "rule": connection.rule or "—",
            "ruleName": self._entries.get(connection.rule_entry_id, "")
            if connection.rule_entry_id
            else "",
            "chain": chain(connection) or "—",
            "download": fmt.size(connection.download_bytes),
            "upload": fmt.size(connection.upload_bytes),
            "downRate": fmt.rate(tracked.download_rate),
            "upRate": fmt.rate(tracked.upload_rate),
            "started": datetime.fromtimestamp(started).strftime("%H:%M:%S"),
            "duration": fmt.uptime(int(self._clock() - started)),
        }
