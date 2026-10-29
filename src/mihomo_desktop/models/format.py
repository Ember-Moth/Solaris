"""Text for numbers the screens show: rates, sizes, durations, latency and dates."""

from __future__ import annotations

import time

_UNITS = ("B", "KB", "MB", "GB", "TB")


def size(byte_count: int) -> str:
    value = float(max(byte_count, 0))
    unit = 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value /= 1024
        unit += 1
    if unit == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {_UNITS[unit]}" if value < 100 else f"{value:.0f} {_UNITS[unit]}"


def rate(bytes_per_second: int) -> str:
    return size(bytes_per_second) + "/s"


def uptime(seconds: int) -> str:
    seconds = max(seconds, 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


GOOD_MS = 150
FAIR_MS = 300


def latency(delay_ms: int | None) -> tuple[str, str]:
    """Return the text and level ("good", "fair", "poor" or "timeout") of a delay."""
    if delay_ms is None:
        return "超时", "timeout"
    if delay_ms < GOOD_MS:
        return f"{delay_ms} ms", "good"
    if delay_ms < FAIR_MS:
        return f"{delay_ms} ms", "fair"
    return f"{delay_ms} ms", "poor"


def usage(used_bytes: int, total_bytes: int) -> str:
    """``38.2 / 200 GB``; the unit is written once when both sides share it."""
    used, total = size(used_bytes), size(total_bytes)
    used_value, _, used_unit = used.partition(" ")
    total_value, _, total_unit = total.partition(" ")
    if used_unit == total_unit:
        return f"{used_value} / {total_value} {total_unit}"
    return f"{used} / {total}"


def date(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def ago(timestamp: float, now: float) -> str:
    """When something happened: ``刚刚``, ``5 分钟前``, ``2 小时前``, ``3 天前``, then a date."""
    seconds = max(now - timestamp, 0)
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)} 天前"
    then = time.localtime(timestamp)
    if then.tm_year == time.localtime(now).tm_year:
        return f"{then.tm_mon} 月 {then.tm_mday} 日"
    return date(timestamp)
