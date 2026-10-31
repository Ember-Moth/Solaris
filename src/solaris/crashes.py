"""Local crash capture and explicit raw export. No uploads, no profile/config collection.

Only the application's own crash directory is pruned. OS reports remain OS-managed;
privileged reports are not made user-readable just for diagnostics.
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

from solaris import __version__

MAX_FILES = 20
MAX_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_AGE_S = 30 * 86400
MAX_TRACE_BYTES = 1024 * 1024
_active: Recorder | None = None
_OWN_NAME = re.compile(r"(?:ui-(?:exception|fatal)|go)-[0-9-]+\.log\Z")


def _regular(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and not getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            return info
    except OSError:
        pass
    return None


def _safe_directory(path: Path) -> bool:
    # Also reject links in ancestors: a Windows reparse point must not steer collection.
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            return False
    return path.is_dir()


def prune(directory: Path, *, now: float | None = None, active: Path | None = None) -> None:
    """Bound our retained files at launch and after each exception, never follow links."""
    if not _safe_directory(directory):
        return
    if active is None and _active is not None:
        active = _active._fatal_path
    now = time.time() if now is None else now
    files = []
    for path in directory.iterdir():
        if path == active or not _OWN_NAME.fullmatch(path.name):
            continue
        if (info := _regular(path)) is not None:
            files.append((info.st_mtime, info.st_size, path))
    count = size = 0
    for modified, length, path in sorted(files, reverse=True):
        stale = modified < now - MAX_AGE_S
        empty = length == 0 and modified < now - 86400
        if stale or empty or count >= MAX_FILES or size + length > MAX_BYTES:
            with contextlib.suppress(OSError):
                path.unlink()
        elif length:
            count += 1
            size += length


def prune_native(directory: Path, *, now: float | None = None) -> None:
    """Only our WER subfolder; macOS reports and other applications are never deleted."""
    if not _safe_directory(directory):
        return
    now = time.time() if now is None else now
    size = count = 0
    files = []
    for path in directory.iterdir():
        if (
            re.fullmatch(r"Solaris\.exe\.[0-9]+\.dmp", path.name, re.IGNORECASE)
            and (info := _regular(path)) is not None
        ):
            files.append((info.st_mtime, info.st_size, path))
    for modified, length, path in sorted(files, reverse=True):
        if modified < now - MAX_AGE_S or count >= 5 or size + length > MAX_BYTES:
            with contextlib.suppress(OSError):
                path.unlink()
        else:
            count += 1
            size += length


class Recorder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._fatal: BinaryIO | None = None
        self._fatal_path: Path | None = None
        self._sys_hook = sys.excepthook
        self._thread_hook = threading.excepthook
        self._lock = threading.Lock()
        self._closed = False

    def _new(self, kind: str) -> tuple[Path, BinaryIO]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _safe_directory(self.directory):
            raise OSError("unsafe crash directory")
        path = self.directory / f"ui-{kind}-{time.time_ns()}-{os.getpid()}.log"
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        return path, os.fdopen(fd, "wb", buffering=0)

    def install(self) -> Recorder:
        global _active
        try:
            self._fatal_path, self._fatal = self._new("fatal")
            prune(self.directory, active=self._fatal_path)
            if sys.platform == "win32":
                native = self.directory / "native"
                native.mkdir(exist_ok=True, mode=0o700)
                prune_native(native)
            faulthandler.enable(self._fatal, all_threads=True, c_stack=False)
        except OSError, RuntimeError:
            # An unwritable/full disk cannot prevent the app starting.
            if self._fatal is not None:
                self._fatal.close()
            self._fatal = None
        sys.excepthook = self._exception
        threading.excepthook = self._thread_exception
        _active = self
        return self

    def record(self, error: BaseException, *, origin: str = "unhandled") -> None:
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            return
        with self._lock:
            if self._closed:
                return
            try:
                # Raw tracebacks can contain paths, messages and source lines; never put
                # them in the ordinary diagnostics summary or inspect local variables.
                trace = "".join(traceback.format_exception(error))
                data = (
                    f"Solaris {__version__}\nPython {sys.version.split()[0]}\nOrigin: {origin}\n\n"
                    + trace
                ).encode("utf-8", errors="replace")
                _path, file = self._new("exception")
                with file:
                    file.write(data[:MAX_TRACE_BYTES])
                    if len(data) > MAX_TRACE_BYTES:
                        file.write(b"\n[truncated]\n")
                prune(self.directory, active=self._fatal_path)
            except Exception:
                pass  # the crash logger must never replace the original failure

    def _exception(
        self, kind: type[BaseException], error: BaseException, trace: TracebackType | None
    ) -> None:
        self.record(error)
        self._sys_hook(kind, error, trace)

    def _thread_exception(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            self.record(args.exc_value, origin="thread")
        self._thread_hook(args)

    def close(self) -> None:
        global _active
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if _active is self:
                _active = None
                sys.excepthook, threading.excepthook = self._sys_hook, self._thread_hook
                if self._fatal is not None:
                    faulthandler.disable()
            if self._fatal is not None:
                self._fatal.close()
            if self._fatal_path is not None:
                info = _regular(self._fatal_path)
                if info is not None and info.st_size == 0:
                    with contextlib.suppress(OSError):
                        self._fatal_path.unlink()


def record(error: BaseException, *, origin: str = "asyncio") -> None:
    if _active is not None:
        _active.record(error, origin=origin)


def asyncio_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    error = context.get("exception")
    if isinstance(error, BaseException):
        record(error)
    else:
        record(RuntimeError(str(context.get("message", "unhandled asyncio error"))))
    loop.default_exception_handler(context)


@dataclass(frozen=True)
class Report:
    path: Path
    kind: str
    size: int
    modified: float


@dataclass(frozen=True)
class Inventory:
    reports: tuple[Report, ...]
    unavailable: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        # No filenames, process arguments, paths, exception messages, or raw contents.
        return {
            "reports": [
                {"kind": report.kind, "bytes": report.size, "modified_at": int(report.modified)}
                for report in self.reports
            ],
            "unavailable_sources": list(self.unavailable),
            "raw_included": False,
        }


def _native_identity(path: Path) -> bool:
    """Only this user's packaged Solaris process, never other Python/Qt applications."""
    data = _read(path, min(MAX_REPORT_BYTES, 128 * 1024), allow_truncated=True)
    if path.suffix == ".ips":
        try:
            header = json.loads(data.split(b"\n", 1)[0])
        except ValueError, UnicodeError:
            return False
        return header.get("bundleID") in ("dev.tschen.solaris", "dev.tschen.solaris.tunnel")
    return bool(
        re.search(rb"^Identifier:\s+dev\.tschen\.solaris(?:\.tunnel)?\s*$", data, re.MULTILINE)
    )


def _read(path: Path, maximum: int = MAX_REPORT_BYTES, *, allow_truncated: bool = False) -> bytes:
    before = _regular(path)
    if before is None or not _safe_directory(path.parent):
        raise OSError("report is not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as file:
        opened = os.fstat(file.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            raise OSError("report changed during export")
        data = file.read(maximum + 1)
    if len(data) > maximum and not allow_truncated:
        raise OSError("report exceeds the export size limit")
    return data[:maximum]


def collect(
    directory: Path,
    *,
    sources: Sequence[tuple[Path, str]] | None = None,
) -> Inventory:
    if sources is None:
        sources = default_sources(directory)
    reports: list[Report] = []
    unavailable: list[str] = []
    now = time.time()
    for folder, kind in sources:
        try:
            folder.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            unavailable.append(kind)
            continue
        if not _safe_directory(folder):
            unavailable.append(kind)
            continue
        try:
            paths = list(folder.iterdir())
        except OSError:
            unavailable.append(kind)
            continue
        for path in paths:
            info = _regular(path)
            if info is None or not 0 < info.st_size <= MAX_REPORT_BYTES:
                continue
            if info.st_mtime < now - MAX_AGE_S:
                continue
            if kind == "macos":
                if path.suffix not in (".ips", ".crash") or not path.name.startswith(
                    ("SolarisUI-", "Solaris-", "dev.tschen.solaris.tunnel-")
                ):
                    continue
                try:
                    if not _native_identity(path):
                        continue
                except OSError:
                    continue
            elif kind == "windows":
                if not re.fullmatch(r"Solaris\.exe\.[0-9]+\.dmp", path.name, re.IGNORECASE):
                    continue
            elif not _OWN_NAME.fullmatch(path.name):
                continue
            reports.append(Report(path, kind, info.st_size, info.st_mtime))
    selected: list[Report] = []
    size = 0
    for report in sorted(reports, key=lambda item: item.modified, reverse=True):
        if len(selected) >= MAX_FILES or size + report.size > MAX_BYTES:
            continue
        selected.append(report)
        size += report.size
    return Inventory(tuple(selected), tuple(unavailable))


def default_sources(directory: Path) -> list[tuple[Path, str]]:
    sources = [(directory, "ui")]
    if sys.platform == "darwin":
        # The root extension's container and system-wide reports are intentionally not
        # collected under a different user's identity. Administrators can retrieve them
        # separately; see docs/maintenance.md.
        sources.append((Path.home() / "Library/Logs/DiagnosticReports", "macos"))
    elif sys.platform == "win32":
        prune_native(directory / "native")
        sources.append((directory / "native", "windows"))
        from solaris.desktop.windows_identity import current_sid

        sid = current_sid()
        if sid:
            root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Solaris"
            sources.append((root / "users" / sid / "crashes", "service"))
    return sources


def archive_name(now: float) -> str:
    return f"solaris-crashes-{datetime.fromtimestamp(now):%Y%m%d-%H%M%S}.zip"


def write_archive(path: Path, inventory: Inventory) -> None:
    """Only called after the user chooses raw export; it never uploads an archive."""
    if path.resolve() in {report.path.resolve() for report in inventory.reports}:
        raise OSError("cannot overwrite a source report")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    partial = Path(temporary)
    try:
        with (
            os.fdopen(fd, "wb") as stream,
            zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive,
        ):
            summary = inventory.summary() | {"raw_included": True}
            archive.writestr("summary.json", json.dumps(summary, indent=2) + "\n")
            size = 0
            for index, report in enumerate(inventory.reports, 1):
                data = _read(report.path)
                size += len(data)
                if size > MAX_BYTES:
                    raise OSError("reports exceed the export size limit")
                # Avoid personal machine names from OS report filenames in archive paths.
                archive.writestr(f"{index:02d}-{report.kind}{report.path.suffix}", data)
        partial.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        raise


def clear(directory: Path) -> None:
    """Clear only UI-owned logs; OS and privileged service reports retain their owners."""
    if not _safe_directory(directory):
        return
    active = _active._fatal_path if _active is not None else None
    for path in directory.iterdir():
        if path != active and _OWN_NAME.fullmatch(path.name) and _regular(path) is not None:
            path.unlink()
