from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtTest import QAbstractItemModelTester
from pytestqt.qtbot import QtBot

from mihomo_desktop.host.base import LogEntry
from mihomo_desktop.models.logs import (
    LogBuffer,
    clock_time,
    export_text,
    subscription_level,
)
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.logs import LogListModel, LogsViewModel
from tests.support.simulated import SimulatedHost

NOW = datetime(2026, 10, 29, 12, 4, 1).timestamp()


def prop(view_model: Any, name: str) -> Any:
    return view_model.property(name)


def entry(level: str, message: str, ms: int = 123) -> LogEntry:
    return LogEntry(level, message, int(NOW * 1000) + ms)


class ScriptedHost(SimulatedHost):
    """Serves the log lines a test queues, and records the level of every poll."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: NOW)
        self.queued: list[LogEntry] = []
        self.levels: list[str] = []

    async def poll_logs(self, level: str) -> list[LogEntry]:
        self.levels.append(level)
        lines, self.queued = self.queued, []
        return lines


# -- models -----------------------------------------------------------------------------------
def test_buffer_keeps_the_most_recent_lines() -> None:
    buffer = LogBuffer(limit=3)
    buffer.extend(entry("info", f"line {n}") for n in range(5))
    assert [line.entry.message for line in buffer.lines()] == ["line 2", "line 3", "line 4"]
    assert (buffer.first_seq, buffer.next_seq, len(buffer)) == (2, 5, 3)
    assert [line.seq for line in buffer.since(4)] == [4]
    assert [line.seq for line in buffer.since(0)] == [2, 3, 4]  # dropped lines are gone
    buffer.clear()
    assert (buffer.first_seq, len(buffer)) == (5, 0)


def test_level_and_search_filter() -> None:
    buffer = LogBuffer()
    buffer.extend(
        [
            entry("debug", "[DNS] github.com --> 198.18.0.12"),
            entry("info", "[TCP] 198.18.0.1:52344 --> github.com:443"),
            entry("warning", "[TCP] dial 节点选择 error: i/o timeout"),
            entry("error", "[Provider] pull error: 403 Forbidden"),
        ]
    )
    messages = [line.entry.level for line in buffer.matching("info", "")]
    assert messages == ["info", "warning", "error"]
    assert [line.entry.level for line in buffer.matching("debug", "GITHUB")] == ["debug", "info"]
    assert [line.entry.level for line in buffer.matching("warning", " error ")] == [
        "warning",
        "error",
    ]


def test_debug_is_the_only_filter_that_subscribes_below_info() -> None:
    assert subscription_level("debug") == "debug"
    assert [subscription_level(level) for level in ("info", "warning", "error")] == ["info"] * 3


def test_export_text() -> None:
    lines = [entry("warning", "[TCP] dial error", 7), entry("info", "started", 1007)]
    assert clock_time(lines[0].time_unix_ms) == "12:04:01"
    assert export_text(lines) == (
        "2026-10-29 12:04:01.007 WARN  [TCP] dial error\n2026-10-29 12:04:02.007 INFO  started\n"
    )


def test_list_model_follows_the_buffer(qtbot: QtBot) -> None:
    buffer = LogBuffer(limit=4)
    model = LogListModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.reset(buffer, "info", "")
    for n in range(10):
        buffer.extend([entry("debug" if n % 2 else "info", f"line {n}")])
        model.follow(buffer, "info", "")
    # The buffer holds lines 6 to 9; the model shows the info ones among them.
    assert [e.message for e in model.entries] == ["line 6", "line 8"]
    roles = {bytes(name.data()).decode(): role for role, name in model.roleNames().items()}
    index = model.index(1)
    assert model.data(index, roles["label"]) == "INFO"
    assert model.data(index, roles["time"]) == "12:04:01"
    model.reset(buffer, "debug", "9")
    assert [e.message for e in model.entries] == ["line 9"]


# -- LogsViewModel ----------------------------------------------------------------------------
@pytest.fixture
def runner(qtbot: QtBot) -> Iterator[AsyncRunner]:
    built = AsyncRunner()
    built.start()
    yield built
    built.shutdown()


@pytest.fixture
def scripted(runner: AsyncRunner) -> Iterator[tuple[ScriptedHost, LogsViewModel]]:
    host = ScriptedHost()
    logs = LogsViewModel(host, runner, lambda: NOW, limit=50)
    yield host, logs
    logs.stop()


def test_level_filter_shows_and_hides_held_lines(
    qtbot: QtBot, scripted: tuple[ScriptedHost, LogsViewModel]
) -> None:
    host, logs = scripted
    host.queued = [entry("info", "a"), entry("warning", "b"), entry("error", "c")]
    logs.start()
    qtbot.waitUntil(lambda: prop(logs, "count") == 3)
    assert prop(logs, "status") == "3 条"
    assert host.levels[0] == "info"

    logs.setLevel("error")
    assert prop(logs, "count") == 1
    logs.setLevel("warning")
    assert prop(logs, "count") == 2  # nothing was lost by filtering

    logs.setLevel("debug")
    qtbot.waitUntil(lambda: host.levels[-1] == "debug")
    host.queued = [entry("debug", "d")]
    qtbot.waitUntil(lambda: prop(logs, "count") == 4)
    logs.setLevel("info")
    qtbot.waitUntil(lambda: host.levels[-1] == "info")
    assert prop(logs, "count") == 3

    logs.setQuery("nothing like this")
    assert prop(logs, "emptyText") == "没有匹配的日志"
    logs.setQuery("B")
    assert prop(logs, "count") == 1


def test_pause_holds_new_lines_until_resumed(
    qtbot: QtBot, scripted: tuple[ScriptedHost, LogsViewModel]
) -> None:
    host, logs = scripted
    logs.start()
    qtbot.waitUntil(lambda: len(host.levels) > 0)
    assert prop(logs, "emptyText") == "没有日志"
    logs.togglePause()
    assert prop(logs, "status") == "已暂停"
    assert prop(logs, "statusTone") == "warning"
    host.queued = [entry("info", "a"), entry("debug", "hidden"), entry("info", "b")]
    qtbot.waitUntil(lambda: prop(logs, "status") == "已暂停 · 2 条新日志")
    assert prop(logs, "count") == 0
    logs.togglePause()
    assert prop(logs, "count") == 2
    assert prop(logs, "statusTone") == ""

    logs.clear()
    assert prop(logs, "count") == 0
    assert prop(logs, "status") == "0 条"


def test_buffer_limit_drops_the_oldest_lines(
    qtbot: QtBot, scripted: tuple[ScriptedHost, LogsViewModel]
) -> None:
    host, logs = scripted
    host.queued = [entry("info", f"line {n}") for n in range(80)]
    logs.start()
    qtbot.waitUntil(lambda: prop(logs, "count") == 50)
    assert logs.model.entries[0].message == "line 30"  # pyright: ignore[reportAttributeAccessIssue]


def test_export_writes_the_lines_shown(
    qtbot: QtBot, scripted: tuple[ScriptedHost, LogsViewModel], tmp_path: Path
) -> None:
    host, logs = scripted
    host.queued = [entry("info", "kept"), entry("error", "also kept"), entry("debug", "hidden")]
    logs.start()
    qtbot.waitUntil(lambda: prop(logs, "count") == 2)
    assert prop(logs, "exportName") == "mihomo-20261029-120401.log"

    target = tmp_path / "mihomo.log"
    logs.exportTo(QUrl.fromLocalFile(str(target)).toString())
    assert target.read_text(encoding="utf-8").splitlines() == [
        "2026-10-29 12:04:01.123 INFO  kept",
        "2026-10-29 12:04:01.123 ERROR also kept",
    ]
    assert prop(logs, "status") == "已导出 2 条"

    logs.exportTo(QUrl.fromLocalFile(str(tmp_path)).toString())  # a directory
    assert prop(logs, "status").startswith("导出失败")
    assert prop(logs, "statusTone") == "danger"


def test_logs_in_english(
    qtbot: QtBot, scripted: tuple[ScriptedHost, LogsViewModel], english: None
) -> None:
    host, logs = scripted
    host.queued = [entry("info", "a")]
    logs.start()
    qtbot.waitUntil(lambda: prop(logs, "count") == 1)
    assert prop(logs, "status") == "1 line"
    labels = [option["label"] for option in prop(logs, "levelOptions")]
    assert labels == ["Debug", "Info", "Warning", "Error"]
    logs.togglePause()
    host.queued = [entry("info", "b"), entry("info", "c")]
    qtbot.waitUntil(lambda: prop(logs, "status") == "Paused · 2 new lines")


def test_simulated_host_logs_every_level(qtbot: QtBot, runner: AsyncRunner) -> None:
    now = [NOW]
    logs = LogsViewModel(SimulatedHost(clock=lambda: now[0]), runner, lambda: now[0])
    logs.setLevel("debug")
    logs.start()
    qtbot.waitUntil(lambda: prop(logs, "count") > 0)
    now[0] += 60  # a minute of visits and occasional warnings
    qtbot.waitUntil(lambda: prop(logs, "count") > 20)
    levels = {e.level for e in logs.model.entries}  # pyright: ignore[reportAttributeAccessIssue]
    assert levels == {"debug", "info", "warning", "error"}
    logs.stop()
