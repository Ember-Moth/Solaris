"""A stand-in for the iCloud calls of SolarisManager.dylib (models/backup_icloud.py ICloud): the
records in a dict, the account and the failures as a test sets them, and the threads it was
called on."""

from __future__ import annotations

import json
import threading

from solaris.host.extension import FAILED, NOT_FOUND, NOT_SIGNED_IN, ManagerError
from solaris.models.backup_icloud import AVAILABLE, UNSUPPORTED


class FakeICloud:
    def __init__(self) -> None:
        self.status = AVAILABLE
        self.records: dict[str, tuple[str, bytes]] = {}  # name → (info, snapshot)
        self.failures: dict[str, ManagerError] = {}  # by call, raised once
        self.calls: list[str] = []
        self.threads: set[threading.Thread] = set()
        self._lock = threading.Lock()

    def _record(self, call: str) -> None:
        with self._lock:
            self.calls.append(call)
            self.threads.add(threading.current_thread())
            failure = self.failures.pop(call, None)
        if failure is not None:
            raise failure

    def _account(self) -> None:
        """As CloudKit answers a build without the entitlements, or a signed-out system."""
        if self.status == UNSUPPORTED:
            raise ManagerError(FAILED, "This build of Solaris is not signed for iCloud")
        if self.status != AVAILABLE:
            raise ManagerError(NOT_SIGNED_IN, "Could not reach iCloud: not authenticated")

    def icloud_status(self, timeout: float) -> int:
        self._record("status")
        return self.status

    def icloud_save(self, name: str, info: str, snapshot: bytes, timeout: float) -> None:
        self._record("save")
        self._account()
        with self._lock:
            self.records[name] = (info, snapshot)

    def icloud_list(self, timeout: float) -> str:
        self._record("list")
        self._account()
        with self._lock:
            listed = [{"name": name, "info": info} for name, (info, _) in self.records.items()]
        return json.dumps(listed)

    def icloud_download(self, name: str, timeout: float) -> bytes:
        self._record("download")
        self._account()
        with self._lock:
            if name not in self.records:
                raise ManagerError(NOT_FOUND, f"No snapshot {name} in iCloud")
            return self.records[name][1]

    def icloud_delete(self, name: str, timeout: float) -> None:
        self._record("delete")
        self._account()
        with self._lock:
            self.records.pop(name, None)
