"""Backup ViewModel: the settings page's 备份与同步 group (docs/storage.md §3, §5, docs/ui.md §8).

A snapshot goes to a file and comes back from one, and on macOS to iCloud: the row turns the
iCloud target on and off, shows its last backup, backs up now, restores from it and deletes
its snapshots. A backup is not encrypted: exporting and turning iCloud on say so first.

Restoring asks whether to replace or merge (restoreRequested, cloudRestoreRequested) when
this machine has profiles; without any, the backup's data comes in as it is. Then the library
downloads and checks every profile again, the settings are read again, and ``restored`` tells
the other pages to show the new data.

With a cloud provider, automatic backup (AutoBackup) runs on a timer: every TICK_MS it looks
whether the database changed and uploads once a change has settled; at start and every
CATCH_UP_S it catches up with the targets that are a day behind or failed last time. iCloud
waits while the system's account cannot be used. One cloud task runs at a time: turning a
target off or deleting its snapshots waits for an upload in flight.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
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
    SnapshotInfo,
    read_file,
    write_file,
)
from solaris.models.backup_auto import AutoBackup, BackupTargets, Changes, Outcome, Target
from solaris.models.backup_icloud import (
    ACCOUNT,
    AVAILABLE,
    ICLOUD,
    NO_ACCOUNT,
    RESTRICTED,
    TEMPORARILY_UNAVAILABLE,
    UNSUPPORTED,
    ICloudProvider,
)
from solaris.models.backup_providers import BackupEntry, BackupProvider
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import ProfileError, ProfileList
from solaris.models.settings import SettingsStore
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

NOTICE_MS = 6000
TICK_MS = 5000
CATCH_UP_S = 3600.0
# System Settings > Apple Account, where the system signs in to iCloud.
ICLOUD_SETTINGS = "x-apple.systempreferences:com.apple.systempreferences.AppleIDSettings"

_FAILURES = {
    "not_a_backup": QT_TRANSLATE_NOOP("BackupViewModel", "这不是 Solaris 的备份文件"),
    "newer_backup": QT_TRANSLATE_NOOP(
        "BackupViewModel", "这份备份来自更新版本的 Solaris，请先更新"
    ),
    "too_large": QT_TRANSLATE_NOOP("BackupViewModel", "备份文件过大"),
    # The providers' (backup_icloud.py); cloud_failed shows its message as it is.
    "not_signed_in": QT_TRANSLATE_NOOP("BackupViewModel", "账户未登录"),
    "backup_missing": QT_TRANSLATE_NOOP("BackupViewModel", "这份备份已不存在"),
    "cloud_full": QT_TRANSLATE_NOOP("BackupViewModel", "储存空间已满"),
    "cloud_unreachable": QT_TRANSLATE_NOOP("BackupViewModel", "无法连接，稍后会再试"),
}
# What the iCloud row says of the system's account when it cannot be used.
_ICLOUD_STATES = {
    UNSUPPORTED: QT_TRANSLATE_NOOP("BackupViewModel", "仅签名的安装版可用"),
    NO_ACCOUNT: QT_TRANSLATE_NOOP("BackupViewModel", "系统未登录 iCloud"),
    RESTRICTED: QT_TRANSLATE_NOOP("BackupViewModel", "iCloud 受到限制"),
    TEMPORARILY_UNAVAILABLE: QT_TRANSLATE_NOOP("BackupViewModel", "iCloud 暂时不可用"),
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
ENABLING = "enabling"
BACKING_UP = "backing_up"
LISTING = "listing"
DOWNLOADING = "downloading"
DELETING = "deleting"
DISABLING = "disabling"
_CLOUD_TASKS = {
    ENABLING: QT_TRANSLATE_NOOP("BackupViewModel", "正在备份…"),
    BACKING_UP: QT_TRANSLATE_NOOP("BackupViewModel", "正在备份…"),
    LISTING: QT_TRANSLATE_NOOP("BackupViewModel", "正在读取 iCloud 中的备份…"),
    DOWNLOADING: QT_TRANSLATE_NOOP("BackupViewModel", "正在下载备份…"),
    DELETING: QT_TRANSLATE_NOOP("BackupViewModel", "正在删除 iCloud 中的备份…"),
}
FILE = "file"  # where a restore comes from: a file, or a provider's kind


def _call[T](write: Callable[[], T]) -> T:
    return write()


def _local_path(url: str) -> Path:
    """The file a FileDialog chose (a file: URL), or a path as it is."""
    return Path(QUrl(url).toLocalFile() or url)


@QmlElement
@QmlUncreatable("BackupViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class BackupViewModel(QObject):
    changed = Signal()
    restoreRequested = Signal()  # the view asks whether to replace or merge
    cloudRestoreRequested = Signal()  # the view asks which snapshot, and replace or merge
    restored = Signal()  # the profiles, the routing and the settings may all have changed

    def __init__(
        self,
        backups: Backups,
        library: ProfileLibrary,
        settings: SettingsStore,
        runner: AsyncRunner,
        *,
        clock: Callable[[], float] = time.time,
        targets: BackupTargets | None = None,
        changes: Changes | None = None,
        icloud: ICloudProvider | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Automatic backup needs ``targets``, ``changes`` and a provider: ``icloud``, the
        provider of a packaged macOS app. Without one, the iCloud row shows on macOS
        all the same and says what it needs."""
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
        self._restoring = FILE  # where the restore in progress comes from
        self._pending: tuple[Path, Snapshot, Counts] | None = None  # awaiting replace or merge
        self._mode = MERGE
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._clear_export_notice)
        # iCloud and automatic backup
        self._icloud = icloud
        self._icloud_shown = icloud is not None or sys.platform == "darwin"
        self._icloud_status: int | None = None if icloud else UNSUPPORTED  # None: not read yet
        self._targets_table = targets
        self._changes = changes
        self._auto: AutoBackup | None = None
        if icloud is not None and targets is not None and changes is not None:
            self._auto = AutoBackup(backups, targets, self._provider, changes=changes, clock=clock)
        # Held on the asyncio thread by each cloud task, automatic or not, for all of it.
        self._cloud_lock = asyncio.Lock()
        self._targets: tuple[Target, ...] = ()
        self._auto_running = False
        self._caught_up_at: float | None = None
        self._minute = -1
        self._cloud_notice = ""
        self._cloud_failed = False
        self._cloud_entries: list[BackupEntry] = []  # awaiting a pick in the restore dialog
        self._cloud_pick = 0
        self._cloud_local: Counts | None = None
        self._cloud_notice_timer = QTimer(self)
        self._cloud_notice_timer.setSingleShot(True)
        self._cloud_notice_timer.setInterval(NOTICE_MS)
        self._cloud_notice_timer.timeout.connect(self._clear_cloud_notice)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)

    def retranslate(self) -> None:
        self.changed.emit()

    def _failure(self, error: BaseException) -> str:
        if isinstance(error, OSError):
            return error.strerror or str(error)
        if isinstance(error, BackupError):
            if error.code in _FAILURES:
                return self.tr(_FAILURES[error.code])
            if error.code == "cloud_failed":
                return error.message
        return failure_text(error)

    def _stored_failure(self, stored: str) -> str:
        """A target's last error as AutoBackup stores it, ``<code>: <message>``."""
        code, _, message = stored.partition(": ")
        if code in _FAILURES:
            return self.tr(_FAILURES[code])
        return message or stored

    def _counts(self, counts: Counts) -> str:
        return self.tr("{profiles}、{plans}").format(
            profiles=self.tr("%n 份订阅", "", counts.profiles),
            plans=self.tr("%n 套分流方案", "", counts.plans),
        )

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._task is not None

    def _snapshot_when(self, info: SnapshotInfo) -> str:
        return f"{fmt.date(info.created_at)} {datetime.fromtimestamp(info.created_at):%H:%M}"

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
        if self._task == RESTORING and self._restoring == FILE:
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
                self._restore(path.name, snapshot, REPLACE)
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
        return f"{info.device} · {self._snapshot_when(info)}"

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
        self._restore(path.name, snapshot, self._mode)

    @Slot()
    def cancelRestore(self) -> None:
        self._pending = None
        self.changed.emit()

    def _restore(self, name: str, snapshot: Snapshot, mode: str, source: str = FILE) -> None:
        """Restore ``snapshot``, from the file ``name`` or a provider's kind ``source``; the
        notice goes to that one's row."""
        self._task = RESTORING
        self._restoring = source
        notify = self._show_restore_notice if source == FILE else self._show_cloud_notice
        if source == FILE:
            self._restore_notice = ""
        self.changed.emit()
        written = False

        def write() -> None:
            nonlocal written
            self._backups.restore(snapshot, mode)
            written = True

        async def restore() -> tuple[ProfileList, BaseException | None]:
            try:
                return await self._library.profiles.restore(write), None
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
                text = self.tr("已从 {name} 恢复").format(name=name)
            notify(text, failed=tunnel_error is not None)

        def failed(error: BaseException) -> None:
            if written:  # the data changed even so
                self._finish_restore()
            else:
                self._task = None
            text = self.tr("恢复失败：{detail}").format(detail=self._failure(error))
            notify(text, failed=True)

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

    # -- iCloud and automatic backup ---------------------------------------------------------
    def start(self) -> None:
        """Read the targets and the iCloud account, catch up and back up on the timer. A
        change the last run made shortly before it quit is in no upload yet: looking
        once the debounce is over takes it along."""
        if self._auto is None:
            return
        self._refresh_targets()
        self.refreshCloud()
        self._tick_timer.start()
        self._tick()

    def stop(self) -> None:
        self._tick_timer.stop()

    def _provider(self, target: Target) -> BackupProvider | None:
        """The provider of a target that can take a snapshot now: iCloud only while the
        system's account can be used, so that a signed-out Mac makes no attempts."""
        if target.provider == ICLOUD and self._icloud_status == AVAILABLE:
            return self._icloud
        return None

    async def _own[T](self, write: Callable[[], T]) -> T:
        """A write to the targets' table that is no change of the user's data."""
        changes = self._changes
        return await asyncio.to_thread(changes.own if changes else _call, write)

    def _icloud_target(self) -> Target | None:
        return next((t for t in self._targets if t.provider == ICLOUD), None)

    def _tick(self) -> None:
        auto, changes = self._auto, self._changes
        if auto is None or changes is None:
            return
        now = self._clock()
        if int(now // 60) != self._minute:  # "5 分钟前" moves on
            self._minute = int(now // 60)
            if self._icloud_status != AVAILABLE:
                self.refreshCloud()  # the user may have signed in meanwhile
            self.changed.emit()
        # Not before the account is read: a catch-up meanwhile would find no provider.
        if self._auto_running or self._task is not None or self._icloud_status is None:
            return
        starting = self._caught_up_at is None
        catch_up = starting or now - (self._caught_up_at or now) >= CATCH_UP_S
        if catch_up:
            self._caught_up_at = now
        self._auto_running = True

        async def run() -> tuple[list[Outcome], tuple[Target, ...] | None]:
            async with self._cloud_lock:
                if await asyncio.to_thread(changes.changed) or starting:
                    auto.changed()
                outcomes = await auto.run_due()
                if catch_up:
                    outcomes += await auto.catch_up()
                return outcomes, await self._read_targets() if outcomes else None

        def done(result: tuple[list[Outcome], tuple[Target, ...] | None]) -> None:
            outcomes, targets = result
            self._auto_running = False
            if targets is not None and self._task is None:  # a task shows its own
                self._targets = targets
                self.changed.emit()
            if any(isinstance(o.error, BackupError) for o in outcomes):
                self.refreshCloud()

        def failed(_: BaseException) -> None:  # the database could not be read
            self._auto_running = False

        self._runner.submit(run(), done, failed)

    async def _read_targets(self) -> tuple[Target, ...]:
        table = self._targets_table
        return () if table is None else await asyncio.to_thread(table.targets)

    def _refresh_targets(self) -> None:
        def read(targets: tuple[Target, ...]) -> None:
            self._targets = targets
            self.changed.emit()

        self._runner.submit(self._read_targets(), read, lambda _: None)

    def _finish_cloud(self, targets: tuple[Target, ...]) -> None:
        """A cloud task is over: show the targets as it left them, in the same change."""
        self._task = None
        self._targets = targets
        self.changed.emit()

    @Slot()
    def refreshCloud(self) -> None:
        """Ask again whether the system is signed in to iCloud."""
        icloud = self._icloud
        if icloud is None:
            return

        def read(status: int) -> None:
            if status != self._icloud_status:
                if status == AVAILABLE and self._icloud_status is not None:
                    self._caught_up_at = None  # signed in again: catch up at the next tick
                self._icloud_status = status
                self.changed.emit()

        self._runner.submit(icloud.status(), read, lambda _: None)

    def _show_cloud_notice(self, text: str, *, failed: bool = False) -> None:
        self._cloud_notice = text
        self._cloud_failed = failed
        self._cloud_notice_timer.start()
        self.changed.emit()

    def _clear_cloud_notice(self) -> None:
        self._cloud_notice = ""
        self.changed.emit()

    @Property(bool, constant=True)
    def icloudShown(self) -> bool:
        """macOS only; from source the row says it needs the signed app."""
        return self._icloud_shown

    @Property(bool, notify=changed)
    def icloudOn(self) -> bool:
        return self._icloud_target() is not None

    @Property(bool, notify=changed)
    def icloudSwitchShown(self) -> bool:
        """Not while the system is signed out, unless to turn the target off."""
        return self._icloud_status != NO_ACCOUNT or self._icloud_target() is not None

    @Property(bool, notify=changed)
    def icloudSwitchEnabled(self) -> bool:
        if self._auto is None or self._task is not None:
            return False
        return self._icloud_status == AVAILABLE or self._icloud_target() is not None

    @Property(bool, notify=changed)
    def icloudSettingsShown(self) -> bool:
        """The way to sign the system in, or to look at its account."""
        return self._icloud_status in (NO_ACCOUNT, TEMPORARILY_UNAVAILABLE)

    @Property(bool, notify=changed)
    def icloudMenuShown(self) -> bool:
        """Deleting the snapshots needs the account, whether backup is on or not."""
        return self._auto is not None and self._icloud_status == AVAILABLE

    @Property(str, notify=changed)
    def icloudStatus(self) -> str:
        return self._icloud_caption()[0]

    @Property(bool, notify=changed)
    def icloudWarning(self) -> bool:
        return self._icloud_caption()[1]

    def _icloud_caption(self) -> tuple[str, bool]:
        """The row's second line, and whether it warns."""
        if self._task == RESTORING and self._restoring == ICLOUD:
            return self.tr("正在恢复，并重新下载订阅…"), False
        if self._task in _CLOUD_TASKS:
            return self.tr(_CLOUD_TASKS[self._task]), False
        if self._cloud_notice:
            return self._cloud_notice, self._cloud_failed
        status = AVAILABLE if self._icloud_status is None else self._icloud_status
        if status in _ICLOUD_STATES:
            return self.tr(_ICLOUD_STATES[status]), status != UNSUPPORTED
        if status != AVAILABLE:
            return self.tr("无法确认 iCloud 的状态"), True
        target = self._icloud_target()
        if target is None:
            return self.tr("未开启"), False
        if target.last_error is not None:
            reason = self._stored_failure(target.last_error)
            return self.tr("上次备份失败：{reason}").format(reason=reason), True
        if target.last_backup_at is None:
            return self.tr("尚未备份"), False
        when = fmt.ago(target.last_backup_at, self._clock())
        return self.tr("上次备份：{when} · 成功").format(when=when), False

    @Property(bool, constant=True)
    def cloudShown(self) -> bool:
        """Whether this platform has a cloud row, and so the buttons for all of them."""
        return self._icloud_shown

    @Property(bool, notify=changed)
    def cloudReady(self) -> bool:
        """A target is on and its account can be used."""
        return self._cloud_ready()

    def _cloud_ready(self) -> bool:
        return (
            self._task is None
            and self._icloud_target() is not None
            and self._icloud_status == AVAILABLE
        )

    @Slot(bool)
    def setICloudEnabled(self, on: bool) -> None:
        """Turn the iCloud target on, after the view said what a backup holds, and back up at
        once; or off, keeping its snapshots."""
        auto, table, icloud = self._auto, self._targets_table, self._icloud
        if auto is None or table is None or icloud is None or self._task is not None:
            return
        target = self._icloud_target()
        if on == (target is not None):
            return
        if target is not None:
            # Off at once on the page; the table follows once an upload in flight is over.
            self._task = DISABLING
            self._targets = tuple(t for t in self._targets if t.id != target.id)
            self.changed.emit()

            async def disable() -> tuple[Target, ...]:
                async with self._cloud_lock:
                    await self._own(partial(table.remove, target.id))
                    return await self._read_targets()

            def not_disabled(_: BaseException) -> None:
                self._task = None
                self._refresh_targets()

            self._runner.submit(disable(), self._finish_cloud, not_disabled)
            return
        self._task = ENABLING
        self._cloud_notice = ""
        self.changed.emit()

        async def enable() -> tuple[Target, ...]:
            async with self._cloud_lock:
                await icloud.sign_in()
                added = await self._own(partial(table.add, ICLOUD, ACCOUNT))
                await auto.backup([added])
                return await self._read_targets()

        def failed(error: BaseException) -> None:
            self._task = None
            text = self.tr("无法开启 iCloud 备份：{detail}").format(detail=self._failure(error))
            self._show_cloud_notice(text, failed=True)
            self._refresh_targets()
            self.refreshCloud()

        self._runner.submit(enable(), self._finish_cloud, failed)

    @Slot()
    def backUpNow(self) -> None:
        """Upload to every target, even one that has this data already."""
        auto = self._auto
        if auto is None or not self._cloud_ready():
            return
        self._task = BACKING_UP
        self._cloud_notice = ""
        self.changed.emit()

        async def back_up() -> tuple[list[Outcome], tuple[Target, ...]]:
            async with self._cloud_lock:
                outcomes = await auto.backup(force=True)
                return outcomes, await self._read_targets()

        def done(result: tuple[list[Outcome], tuple[Target, ...]]) -> None:
            outcomes, targets = result
            self._finish_cloud(targets)
            if outcomes and not any(o.uploaded or o.error for o in outcomes):
                self._show_cloud_notice(self.tr("本机还没有可备份的数据"))

        def failed(error: BaseException) -> None:
            self._task = None
            text = self.tr("备份失败：{detail}").format(detail=self._failure(error))
            self._show_cloud_notice(text, failed=True)

        self._runner.submit(back_up(), done, failed)

    @Slot()
    def openCloudRestore(self) -> None:
        """List the snapshots in iCloud; the view then asks which one, and how."""
        icloud = self._icloud
        if icloud is None or not self._cloud_ready():
            return
        self._task = LISTING
        self._cloud_notice = ""
        self.changed.emit()

        async def read() -> tuple[list[BackupEntry], Counts]:
            entries = await icloud.list()
            return list(entries), await asyncio.to_thread(self._backups.counts)

        def done(result: tuple[list[BackupEntry], Counts]) -> None:
            entries, local = result
            self._task = None
            if not entries:
                self._show_cloud_notice(self.tr("iCloud 中还没有备份"))
                return
            self._cloud_entries = entries
            self._cloud_pick = 0
            self._cloud_local = local
            self._mode = MERGE
            self.changed.emit()
            self.cloudRestoreRequested.emit()

        def failed(error: BaseException) -> None:
            self._task = None
            text = self.tr("无法读取备份：{detail}").format(detail=self._failure(error))
            self._show_cloud_notice(text, failed=True)

        self._runner.submit(read(), done, failed)

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def cloudSnapshots(self) -> list[dict[str, str]]:
        """The restore dialog's list, newest first."""
        return [
            {
                "title": self._snapshot_when(entry.info),
                "detail": f"{entry.info.device} · {self._counts(entry.info.counts)}",
                "provider": ACCOUNT,
            }
            for entry in self._cloud_entries
        ]

    @Property(int, notify=changed)
    def cloudSnapshot(self) -> int:
        return self._cloud_pick

    @Slot(int)
    def setCloudSnapshot(self, index: int) -> None:
        if 0 <= index < len(self._cloud_entries) and index != self._cloud_pick:
            self._cloud_pick = index
            self.changed.emit()

    @Property(bool, notify=changed)
    def cloudAsksMode(self) -> bool:
        """With profiles here the dialog asks replace or merge; without, it replaces."""
        return self._cloud_asks_mode()

    def _cloud_asks_mode(self) -> bool:
        return self._cloud_local is not None and self._cloud_local.profiles > 0

    @Property(str, notify=changed)
    def cloudLocalSummary(self) -> str:
        if self._cloud_local is None:
            return ""
        return self.tr("本机已有 {counts}").format(counts=self._counts(self._cloud_local))

    @Slot()
    def confirmCloudRestore(self) -> None:
        """Download the snapshot the view picked and restore it with the mode chosen."""
        icloud = self._icloud
        if icloud is None or not self._cloud_entries or self._task is not None:
            return
        entry = self._cloud_entries[self._cloud_pick]
        mode = self._mode if self._cloud_asks_mode() else REPLACE
        self._cloud_entries, self._cloud_local = [], None
        self._task = DOWNLOADING
        self._cloud_notice = ""
        self.changed.emit()

        def downloaded(snapshot: Snapshot) -> None:
            self._task = None
            self._restore(ACCOUNT, snapshot, mode, ICLOUD)

        def failed(error: BaseException) -> None:
            self._task = None
            text = self.tr("无法恢复：{detail}").format(detail=self._failure(error))
            self._show_cloud_notice(text, failed=True)

        self._runner.submit(icloud.download(entry.id), downloaded, failed)

    @Slot()
    def cancelCloudRestore(self) -> None:
        self._cloud_entries, self._cloud_local = [], None
        self.changed.emit()

    @Slot()
    def deleteICloudBackups(self) -> None:
        """Delete every snapshot in iCloud. A target that stays on uploads again with the next
        change or start, so the row no longer claims a backup."""
        icloud, table = self._icloud, self._targets_table
        if icloud is None or table is None or self._task is not None:
            return
        target = self._icloud_target()
        self._task = DELETING
        self._cloud_notice = ""
        self.changed.emit()

        async def delete() -> tuple[Target, ...]:
            # After an upload in flight, so that its snapshot goes too. Forgetting is no
            # change of the data: the next upload waits for one, or for the next start.
            async with self._cloud_lock:
                try:
                    for entry in await icloud.list():
                        await icloud.delete(entry.id)
                finally:
                    if target is not None:
                        await self._own(partial(table.forget, target.id))
                return await self._read_targets()

        def done(targets: tuple[Target, ...]) -> None:
            # The forgotten target counts as never backed up: not caught up at once, but
            # with the next change or the next hour's catch-up.
            self._caught_up_at = self._clock()
            self._finish_cloud(targets)
            self._show_cloud_notice(self.tr("已删除 iCloud 中的备份"))

        def failed(error: BaseException) -> None:
            self._task = None
            self._refresh_targets()
            text = self.tr("删除失败：{detail}").format(detail=self._failure(error))
            self._show_cloud_notice(text, failed=True)

        self._runner.submit(delete(), done, failed)

    @Slot()
    def openICloudSettings(self) -> None:
        QDesktopServices.openUrl(QUrl(ICLOUD_SETTINGS))
