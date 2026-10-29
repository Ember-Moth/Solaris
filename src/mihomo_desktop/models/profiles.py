"""Profiles the UI manages. Subscription download and storage are not implemented yet;
the overview only reads a summary of the active profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProfileSummary:
    name: str
    source: str  # "sub.example.com" or "config.yaml"
    updated: str  # "2 小时前"
    used_bytes: int | None  # None when the subscription reports no quota
    total_bytes: int | None
    expires: str | None  # "2026-12-31"


class ProfileSource(Protocol):
    def active(self) -> ProfileSummary | None: ...


class NoProfiles:
    def active(self) -> ProfileSummary | None:
        return None
