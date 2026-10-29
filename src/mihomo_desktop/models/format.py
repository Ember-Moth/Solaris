"""Text for numbers the screens show: rates, sizes, durations and latency."""

from __future__ import annotations

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
