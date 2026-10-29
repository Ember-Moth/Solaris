"""Logs ViewModel: mihomo's log lines, the level and search filters, pause, clear and export.

Lines are collected from the moment the app starts, whichever page shows, so the page has
the lines that led up to a problem. How the level filter relates to the subscription is
described in models/logs.py."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import LOG_LEVELS, LogEntry, TunnelHost
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP
from mihomo_desktop.models.logs import (
    LEVEL_LABELS,
    LOG_LIMIT,
    LogBuffer,
    LogLine,
    clock_time,
    export_text,
    subscription_level,
)
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

POLL_MS = 500
NOTICE_MS = 4000
LEVEL_OPTIONS = {
    "debug": QT_TRANSLATE_NOOP("LogsViewModel", "调试"),
    "info": QT_TRANSLATE_NOOP("LogsViewModel", "信息"),
    "warning": QT_TRANSLATE_NOOP("LogsViewModel", "警告"),
    "error": QT_TRANSLATE_NOOP("LogsViewModel", "错误"),
}

type Index = QModelIndex | QPersistentModelIndex


class LogListModel(QAbstractListModel):
    """The lines the page shows. ``follow`` appends what arrived and drops what the buffer
    dropped; changing a filter resets the model."""

    ROLES = ("time", "level", "label", "message")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lines: list[LogLine] = []
        self._scanned = 0  # the seq after the last line looked at

    @property
    def entries(self) -> list[LogEntry]:
        return [line.entry for line in self._lines]

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._lines)

    def roleNames(self) -> dict[int, QByteArray]:
        first = Qt.ItemDataRole.UserRole + 1
        return {first + n: QByteArray(name.encode()) for n, name in enumerate(self.ROLES)}

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        number = role - Qt.ItemDataRole.UserRole - 1
        if not index.isValid() or not 0 <= number < len(self.ROLES):
            return None
        entry = self._lines[index.row()].entry
        name = self.ROLES[number]
        if name == "time":
            return clock_time(entry.time_unix_ms)
        if name == "label":
            return LEVEL_LABELS[entry.level]
        return entry.level if name == "level" else entry.message

    def reset(self, buffer: LogBuffer, level: str, query: str) -> None:
        self.beginResetModel()
        self._lines = buffer.matching(level, query)
        self._scanned = buffer.next_seq
        self.endResetModel()

    def follow(self, buffer: LogBuffer, level: str, query: str) -> None:
        dropped = 0
        while dropped < len(self._lines) and self._lines[dropped].seq < buffer.first_seq:
            dropped += 1
        if dropped:
            self.beginRemoveRows(QModelIndex(), 0, dropped - 1)
            del self._lines[:dropped]
            self.endRemoveRows()
        needle = query.strip().casefold()
        new = buffer.since(self._scanned)
        if new:
            self._scanned = new[-1].seq + 1
        shown = [line for line in new if line.matches(level, needle)]
        if shown:
            start = len(self._lines)
            self.beginInsertRows(QModelIndex(), start, start + len(shown) - 1)
            self._lines.extend(shown)
            self.endInsertRows()


@QmlElement
@QmlUncreatable("LogsViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class LogsViewModel(QObject):
    changed = Signal()
    filtersChanged = Signal()

    def __init__(
        self,
        host: TunnelHost,
        runner: AsyncRunner,
        clock: Callable[[], float] = time.time,
        limit: int = LOG_LIMIT,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._runner = runner
        self._clock = clock
        self._buffer = LogBuffer(limit)
        self._model = LogListModel(self)
        self._level = "info"
        self._query = ""
        self._paused = False
        self._held = 0  # lines matching the filters that arrived while paused
        self._error = ""
        self._notice = ""
        self._notice_is_error = False
        self._polling = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.poll)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._clear_notice)

    def start(self) -> None:
        self.poll()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._notice_timer.stop()

    @Slot()
    def poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        level = subscription_level(self._level)
        self._runner.submit(self._host.poll_logs(level), self._received, self._failed)

    def _received(self, entries: list[LogEntry]) -> None:
        self._polling = False
        had_error = bool(self._error)
        self._error = ""
        added = self._buffer.extend(entries)
        if self._paused:
            needle = self._query.strip().casefold()
            self._held += sum(1 for line in added if line.matches(self._level, needle))
        elif added:
            self._model.follow(self._buffer, self._level, self._query)
        if added or had_error:
            self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self._polling = False
        self._error = failure_text(error)
        self.changed.emit()

    def _refilter(self) -> None:
        self._held = 0
        self._model.reset(self._buffer, self._level, self._query)
        self.filtersChanged.emit()
        self.changed.emit()

    # -- filters -----------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def model(self) -> LogListModel:
        return self._model

    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def levelOptions(self) -> list[dict[str, str]]:
        return [{"value": level, "label": self.tr(LEVEL_OPTIONS[level])} for level in LOG_LEVELS]

    @Property(str, notify=filtersChanged)
    def level(self) -> str:
        """The lowest level shown."""
        return self._level

    @Slot(str)
    def setLevel(self, level: str) -> None:
        if level in LOG_LEVELS and level != self._level:
            self._level = level
            self._refilter()

    @Property(str, notify=filtersChanged)
    def query(self) -> str:
        return self._query

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self._refilter()

    # -- pause, clear, export ----------------------------------------------------------------
    @Property(bool, notify=changed)
    def paused(self) -> bool:
        return self._paused

    @Slot()
    def togglePause(self) -> None:
        """Pausing freezes the page; lines keep arriving and show once it resumes."""
        self._paused = not self._paused
        if not self._paused:
            self._held = 0
            self._model.follow(self._buffer, self._level, self._query)
        self.changed.emit()

    @Slot()
    def clear(self) -> None:
        self._buffer.clear()
        self._refilter()

    @Property(int, notify=changed)
    def count(self) -> int:
        return self._model.rowCount()

    @Property(str, notify=changed)
    def exportName(self) -> str:
        stamp = datetime.fromtimestamp(self._clock()).strftime("%Y%m%d-%H%M%S")
        return f"mihomo-{stamp}.log"

    @Slot(str)
    def exportTo(self, url: str) -> None:
        """Write the lines the page shows to the file at ``url`` (a file: URL)."""
        entries = self._model.entries
        path = Path(QUrl(url).toLocalFile() or url)
        try:
            path.write_text(export_text(entries), encoding="utf-8", newline="\n")
        except OSError as error:
            detail = error.strerror or str(error)
            self._show_notice(self.tr("导出失败：{detail}").format(detail=detail), error=True)
            return
        self._show_notice(self.tr("已导出 %n 条", "", len(entries)))

    def _show_notice(self, text: str, *, error: bool = False) -> None:
        self._notice = text
        self._notice_is_error = error
        self._notice_timer.start()
        self.changed.emit()

    def _clear_notice(self) -> None:
        self._notice = ""
        self.changed.emit()

    # -- status ------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def status(self) -> str:
        if self._notice:
            return self._notice
        if self._error:
            return self._error
        if self._paused:
            if self._held:
                return self.tr("已暂停 · %n 条新日志", "", self._held)
            return self.tr("已暂停")
        return self.tr("%n 条", "", self._model.rowCount())

    @Property(str, notify=changed)
    def statusTone(self) -> str:
        """``danger`` for errors, ``warning`` while paused, otherwise empty."""
        if (self._notice and self._notice_is_error) or (self._error and not self._notice):
            return "danger"
        return "warning" if self._paused and not self._notice else ""

    @Property(str, notify=changed)
    def emptyText(self) -> str:
        if self._model.rowCount():
            return ""
        if len(self._buffer):
            return self.tr("没有匹配的日志")
        return self.tr("没有日志")
