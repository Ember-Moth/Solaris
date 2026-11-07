"""A stand-in for NSApp's activation policy (desktop/dock.py): it records, and the test
process stays out of the Dock."""

from __future__ import annotations

ACTIVATE = "activate"


class RecordingApplication:
    def __init__(self) -> None:
        self.calls: list[str] = []  # the policies set, and ACTIVATE

    def set_policy(self, policy: str) -> None:
        self.calls.append(policy)

    def activate(self) -> None:
        self.calls.append(ACTIVATE)
