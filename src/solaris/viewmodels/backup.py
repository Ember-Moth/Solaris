"""Backup ViewModel: the settings page's 备份与同步 group (docs/storage.md §3, docs/ui.md §8).

For now a snapshot goes to a file and comes back from one; the iCloud and Google Drive rows
come with their providers. A backup file is not encrypted: exporting says so first.

Restoring asks whether to replace or merge (restoreRequested) when this machine has
profiles; without any, the file's data comes in as it is. Then the library downloads and
checks every profile again, the settings are read again, and ``restored`` tells the other
pages to show the new data.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import HostError
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.models.backup import (
    MERGE,
    REPLACE,
    RESTORE_MODES,
    BackupError,
    Backups,
    Counts,
    Snapshot,
    read_file,
    write_file,
)
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import ProfileError, ProfileList
from solaris.models.settings import SettingsStore
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

NOTICE_MS = 6000

_FAILURES = {
    "not_a_backup": QT_TRANSLATE_NOOP("BackupViewModel", "这不是 Solaris 的备份文件"),
    "newer_backup": QT_TRANSLATE_NOOP(
        "BackupViewModel", "这份备份来自更新版本的 Solaris，请先更新"
    ),
    "too_large": QT_TRANSLATE_NOOP("BackupViewModel", "备份文件过大"),
}
MODE_LABELS = {
    MERGE: (
        QT_TRANSLATE_NOOP("BackupViewModel", "合并"),
        QT_TRANSLATE_NOOP("BackupViewModel", "按记录合并，两边都改过的，以较新的修改为准"),
    ),
    REPLACE: (
        QT_TRANSLATE_NOOP("BackupViewModel", "替换"),
        QT_TRANSLATE_NOOP(
            "BackupViewModel", "本机的订阅、分流方案、覆写和设置全部换成备份里的内容"
        ),
    ),
}

# What the group is doing.
EXPORTING = "exporting"
READING = "reading"
RESTORING = "restoring"


def _local_path(url: str) -> Path:
    """The file a FileDialog chose (a file: URL), or a path as it is."""
    return Path(QUrl(url).toLocalFile() or url)


@QmlElement
@QmlUncreatable("BackupViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class BackupViewModel(QObject):
    changed = Signal()
    restoreRequested = Signal()  # the view asks whether to replace or merge
    restored = Signal()  # the profiles, the routing and the settings may all have changed

    def __init__(
        self,
        backups: Backups,
        library: ProfileLibrary,
        settings: SettingsStore,
        runner: AsyncRunner,
        *,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backups = backups
        self._library = library
        self._settings = settings
        self._runner = runner
        self._clock = clock
        self._task: str | None = None
        self._export_notice = ""
        self._export_failed = False
        self._restore_notice = ""
        self._restore_failed = False
        self._pending: tuple[Path, Snapshot, Counts] | None = None  # awaiting replace or merge
        self._mode = MERGE
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._clear_export_notice)

    def retranslate(self) -> None:
        self.changed.emit()

    def _failure(self, error: BaseException) -> str:
        if isinstance(error, OSError):
            return error.strerror or str(error)
        if isinstance(error, BackupError) and error.code in _FAILURES:
            return self.tr(_FAILURES[error.code])
        return failure_text(error)

    def _counts(self, counts: Counts) -> str:
        return self.tr("{profiles}、{plans}").format(
            profiles=self.tr("%n 份订阅", "", counts.profiles),
            plans=self.tr("%n 套分流方案", "", counts.plans),
        )

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._task is not None

    # -- export ------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def exportName(self) -> str:
        stamp = datetime.fromtimestamp(self._clock()).strftime("%Y%m%d-%H%M%S")
        return f"solaris-backup-{stamp}.json"

    @Property(str, notify=changed)
    def exportStatus(self) -> str:
        if self._task == EXPORTING:
            return self.tr("正在导出…")
        if self._export_notice:
            return self._export_notice
        return self.tr("不加密，其中有订阅链接")

    @Property(bool, notify=changed)
    def exportFailed(self) -> bool:
        return bool(self._export_notice) and self._export_failed

    @Slot(str)
    def exportBackup(self, url: str) -> None:
        """Write a snapshot to the file a FileDialog chose."""
        if self._task is not None:
            return
        path = _local_path(url)
        self._task = EXPORTING
        self.changed.emit()

        async def export() -> None:
            snapshot = await asyncio.to_thread(self._backups.snapshot)
            await asyncio.to_thread(write_file, path, snapshot)

        def exported(_: object) -> None:
            self._task = None
            self._show_export_notice(self.tr("已导出到 {name}").format(name=path.name))

        def failed(error: BaseException) -> None:
            self._task = None
            text = self.tr("导出失败：{detail}").format(detail=self._failure(error))
            self._show_export_notice(text, failed=True)

        self._runner.submit(export(), exported, failed)

    def _show_export_notice(self, text: str, *, failed: bool = False) -> None:
        self._export_notice = text
        self._export_failed = failed
        self._notice_timer.start()
        self.changed.emit()

    def _clear_export_notice(self) -> None:
        self._export_notice = ""
        self.changed.emit()

    # -- restore -----------------------------------------------------------------------------
    @Property(str, notify=changed)
    def restoreStatus(self) -> str:
        if self._task == READING:
            return self.tr("正在读取…")
        if self._task == RESTORING:
            return self.tr("正在恢复，并重新下载订阅…")
        return self._restore_notice

    @Property(bool, notify=changed)
    def restoreFailed(self) -> bool:
        return bool(self._restore_notice) and self._restore_failed

    @Slot(str)
    def openBackup(self, url: str) -> None:
        """Read the file a FileDialog chose. This machine without profiles takes its data
        as it is; otherwise the view asks replace or merge."""
        if self._task is not None:
            return
        path = _local_path(url)
        self._task = READING
        self._restore_notice = ""
        self.changed.emit()

        async def read() -> tuple[Snapshot, Counts]:
            snapshot = await asyncio.to_thread(read_file, path)
            return snapshot, await asyncio.to_thread(self._backups.counts)

        def done(result: tuple[Snapshot, Counts]) -> None:
            snapshot, local = result
            self._task = None
            if local.profiles == 0:
                self._restore(path, snapshot, REPLACE)
                return
            self._pending = (path, snapshot, local)
            self._mode = MERGE
            self.changed.emit()
            self.restoreRequested.emit()

        def failed(error: BaseException) -> None:
            self._task = None
            self._show_restore_notice(
                self.tr("无法恢复：{detail}").format(detail=self._failure(error)), failed=True
            )

        self._runner.submit(read(), done, failed)

    @Property(str, notify=changed)
    def snapshotTitle(self) -> str:
        """Where and when the file's snapshot was taken."""
        if self._pending is None:
            return ""
        info = self._pending[1].info
        when = datetime.fromtimestamp(info.created_at).strftime("%H:%M")
        return f"{info.device} · {fmt.date(info.created_at)} {when}"

    @Property(str, notify=changed)
    def snapshotDetail(self) -> str:
        return "" if self._pending is None else self._counts(self._pending[1].info.counts)

    @Property(str, notify=changed)
    def localSummary(self) -> str:
        if self._pending is None:
            return ""
        return self.tr("本机已有 {counts}").format(counts=self._counts(self._pending[2]))

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def restoreModes(self) -> list[dict[str, str]]:
        modes = []
        for mode in RESTORE_MODES:
            label, detail = MODE_LABELS[mode]
            modes.append({"value": mode, "label": self.tr(label), "detail": self.tr(detail)})
        return modes

    @Property(str, notify=changed)
    def restoreMode(self) -> str:
        return self._mode

    @Slot(str)
    def setRestoreMode(self, mode: str) -> None:
        if mode in RESTORE_MODES and mode != self._mode:
            self._mode = mode
            self.changed.emit()

    @Slot()
    def confirmRestore(self) -> None:
        """Restore the file the view asked about, with the mode chosen."""
        if self._pending is None or self._task is not None:
            return
        path, snapshot, _ = self._pending
        self._pending = None
        self._restore(path, snapshot, self._mode)

    @Slot()
    def cancelRestore(self) -> None:
        self._pending = None
        self.changed.emit()

    def _restore(self, path: Path, snapshot: Snapshot, mode: str) -> None:
        self._task = RESTORING
        self._restore_notice = ""
        self.changed.emit()
        written = False

        def write() -> None:
            nonlocal written
            self._backups.restore(snapshot, mode)
            written = True

        async def restore() -> tuple[ProfileList, BaseException | None]:
            try:
                return await self._library.restore(write), None
            except (HostError, ProfileError) as error:
                if not written:
                    raise
                return await self._library.current(), error  # the tunnel kept what it ran

        def done(result: tuple[ProfileList, BaseException | None]) -> None:
            profiles, tunnel_error = result
            self._finish_restore()
            failed = len(profiles.update_failed)
            if tunnel_error is not None:
                text = self.tr("已恢复，但隧道未能切换：{detail}").format(
                    detail=failure_text(tunnel_error)
                )
            elif failed:
                text = self.tr("已恢复；%n 份订阅更新失败，已在订阅页标出", "", failed)
            else:
                text = self.tr("已从 {name} 恢复").format(name=path.name)
            self._show_restore_notice(text, failed=tunnel_error is not None)

        def failed(error: BaseException) -> None:
            if written:  # the data changed even so
                self._finish_restore()
            else:
                self._task = None
            text = self.tr("恢复失败：{detail}").format(detail=self._failure(error))
            self._show_restore_notice(text, failed=True)

        self._runner.submit(restore(), done, failed)

    def _finish_restore(self) -> None:
        """The data is the restored one now: read the settings again and tell the pages."""
        self._task = None
        self._settings.reload()
        self.restored.emit()

    def _show_restore_notice(self, text: str, *, failed: bool) -> None:
        # Stays until the next restore: the profiles it marks stay marked too.
        self._restore_notice = text
        self._restore_failed = failed
        self.changed.emit()
