"""The logs page's data: the most recent mihomo log lines, the level and search filters and
the export.

The level filter works in two places. The page filters what it shows by level, so moving
the filter shows or hides lines it already holds. Underneath, the subscription asks the
core for info and above, or for debug and above while the filter is on debug: debug lines
are frequent and would push older info lines out of the buffer. So switching the filter to
debug shows debug lines from that moment on, never earlier ones; mihomo's own ``log-level``
setting does not change what the subscription receives."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from mihomo_desktop.host.base import LOG_LEVELS, LogEntry

LOG_LIMIT = 5000  # lines kept; older ones are dropped
# How the page and the export write the level: mihomo's own names, never translated.
LEVEL_LABELS = {"debug": "DEBUG", "info": "INFO", "warning": "WARN", "error": "ERROR"}


def rank(level: str) -> int:
    return LOG_LEVELS.index(level)


def subscription_level(shown: str) -> str:
    """The level to subscribe at while the page shows ``shown`` and above."""
    return "debug" if shown == "debug" else "info"


@dataclass(frozen=True)
class LogLine:
    seq: int  # increases by one per line received, never reused
    entry: LogEntry

    def matches(self, level: str, query: str) -> bool:
        """``query`` is matched against the message, ignoring case; pass it casefolded."""
        if rank(self.entry.level) < rank(level):
            return False
        return not query or query in self.entry.message.casefold()


class LogBuffer:
    def __init__(self, limit: int = LOG_LIMIT) -> None:
        self._lines: deque[LogLine] = deque(maxlen=limit)
        self._next_seq = 0

    def extend(self, entries: Iterable[LogEntry]) -> list[LogLine]:
        """Add lines, dropping the oldest past the limit; returns the added lines."""
        added = []
        for entry in entries:
            line = LogLine(self._next_seq, entry)
            self._next_seq += 1
            self._lines.append(line)
            added.append(line)
        return added

    def clear(self) -> None:
        self._lines.clear()

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def first_seq(self) -> int:
        """The oldest line still held, or the next one to arrive when empty."""
        return self._lines[0].seq if self._lines else self._next_seq

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def lines(self) -> tuple[LogLine, ...]:
        return tuple(self._lines)

    def since(self, seq: int) -> list[LogLine]:
        """The lines from ``seq`` on."""
        start = max(seq - self.first_seq, 0)
        return [self._lines[i] for i in range(start, len(self._lines))]

    def matching(self, level: str, query: str) -> list[LogLine]:
        needle = query.strip().casefold()
        return [line for line in self._lines if line.matches(level, needle)]


def clock_time(time_unix_ms: int) -> str:
    """``12:04:01`` in local time, as the page shows it."""
    return datetime.fromtimestamp(time_unix_ms / 1000).strftime("%H:%M:%S")


def export_text(entries: Iterable[LogEntry]) -> str:
    """One line per entry, with the date and milliseconds the page leaves out:
    ``2026-10-29 12:04:01.123 INFO  [TCP] …``."""
    rows = []
    for entry in entries:
        stamp = datetime.fromtimestamp(entry.time_unix_ms / 1000)
        when = stamp.strftime("%Y-%m-%d %H:%M:%S.") + f"{entry.time_unix_ms % 1000:03d}"
        rows.append(f"{when} {LEVEL_LABELS[entry.level]:<5} {entry.message}\n")
    return "".join(rows)
