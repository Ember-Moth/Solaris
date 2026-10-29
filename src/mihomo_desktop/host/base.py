"""Platform-neutral host boundary the models depend on (docs/architecture.md §7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HostError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class HostInfo:
    protocol_version: int
    service_version: str
    core_version: str
    state: str  # RunState without its prefix, e.g. "stopped"
    ownership: str  # Ownership without its prefix; "unspecified" on macOS


class TunnelHost(Protocol):
    async def get_info(self) -> HostInfo: ...

    async def close(self) -> None: ...
