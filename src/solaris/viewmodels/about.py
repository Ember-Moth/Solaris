"""About ViewModel: the settings page's 关于 group past the versions it shows: checking for
and installing updates, exporting diagnostics and the third-party licenses
(docs/release.md).

An update goes check → download → verify → ready, one step at a time; the button in the
version row does the next one. Opening the installer always asks first (installRequested).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris import diagnostics, licenses
from solaris.host.base import LogEntry, TunnelHost
from solaris.i18n import QT_TRANSLATE_NOOP, current_language
from solaris.models import format as fmt
from solaris.models.library import ProfileLibrary
from solaris.models.settings import UPDATE_CHECK_HOURS, Settings, SettingsError, SettingsStore
from solaris.update.releases import UpdateError
from solaris.update.updater import Check, Downloaded, Updater
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

STARTUP_CHECK_MS = 30_000  # after the UI starts, before the first automatic check
HOUR_MS = 3_600_000
NOTICE_MS = 6000

INTERVAL_LABELS = {
    24: QT_TRANSLATE_NOOP("AboutViewModel", "每天"),
    72: QT_TRANSLATE_NOOP("AboutViewModel", "每 3 天"),
    168: QT_TRANSLATE_NOOP("AboutViewModel", "每周"),
}
_FAILURES = {
    "check_failed": QT_TRANSLATE_NOOP("AboutViewModel", "检查更新失败：{detail}"),
    "bad_release": QT_TRANSLATE_NOOP("AboutViewModel", "发布的版本号无法识别"),
    "no_installer": QT_TRANSLATE_NOOP("AboutViewModel", "这个版本没有适合本机的安装包"),
    "checksum_missing": QT_TRANSLATE_NOOP("AboutViewModel", "发布没有附带校验和"),
    "checksum_mismatch": QT_TRANSLATE_NOOP("AboutViewModel", "下载的文件与校验和不符"),
    "download_failed": QT_TRANSLATE_NOOP("AboutViewModel", "下载失败：{detail}"),
    "signature_invalid": QT_TRANSLATE_NOOP("AboutViewModel", "安装包的签名无效：{detail}"),
    "signer_mismatch": QT_TRANSLATE_NOOP("AboutViewModel", "安装包不是由 Solaris 的开发者签名的"),
}

COMPONENT_KINDS = {
    "python": QT_TRANSLATE_NOOP("AboutViewModel", "Python 依赖"),
    "go": QT_TRANSLATE_NOOP("AboutViewModel", "Go 模块"),
    "qt": QT_TRANSLATE_NOOP("AboutViewModel", "Qt"),
    "font": QT_TRANSLATE_NOOP("AboutViewModel", "字体"),
}


@dataclass(frozen=True)
class ComponentRow:
    id: str
    name: str
    version: str
    license: str
    category: str  # the list's section

    @property
    def componentId(self) -> str:  # QML reserves ``id``
        return self.id


def _roles(row: type) -> tuple[str, ...]:
    return (*(f.name for f in fields(row) if f.name != "id"), "componentId")


# What the version row is doing.
IDLE = "idle"  # not checked since the UI started
CHECKING = "checking"
LATEST = "latest"
AVAILABLE = "available"  # newer, with an installer for this machine
UNAVAILABLE = "unavailable"  # newer, without one
DOWNLOADING = "downloading"
VERIFYING = "verifying"
READY = "ready"
FAILED = "failed"


def open_file(path: Path) -> bool:
    """Open ``path`` the way the system opens it: Finder mounts a disk image, Windows runs an
    installer (and asks for elevation)."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


@QmlElement
@QmlUncreatable("AboutViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class AboutViewModel(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed
    installRequested = Signal()  # the view asks before the installer is opened
    _progressed = Signal(int, int)  # from the download's thread
    _verifying = Signal()

    def __init__(
        self,
        updater: Updater,
        settings: SettingsStore,
        host: TunnelHost,
        library: ProfileLibrary,
        runner: AsyncRunner,
        logs: Callable[[], Sequence[LogEntry]],
        *,
        clock: Callable[[], float] = time.time,
        opener: Callable[[Path], bool] = open_file,
        manifest: Path = licenses.MANIFEST,
        notices: Path = licenses.NOTICES,
        parent: QObject | None = None,
    ) -> None:
        """``logs`` returns the core's log lines the logs page holds; it is called on the
        Qt thread."""
        super().__init__(parent)
        self._updater = updater
        self._settings = settings
        settings.subscribe(self._settings_changed)
        self._host = host
        self._library = library
        self._runner = runner
        self._logs = logs
        self._clock = clock
        self._opener = opener
        self._manifest = manifest
        self._notices = notices
        self._components: tuple[licenses.Component, ...] | None = None
        self._component_query = ""
        self._component_model = KeyedListModel(_roles(ComponentRow), self)

        self._state = IDLE
        self._check: Check | None = None
        self._downloaded: Downloaded | None = None
        self._checked_at = 0.0
        self._error = ""
        self._received = 0
        self._total = 0
        self._cancel = threading.Event()
        self._progressed.connect(self._progress)
        self._verifying.connect(self._verify_started)
        self._startup = QTimer(self)
        self._startup.setSingleShot(True)
        self._startup.setInterval(STARTUP_CHECK_MS)
        self._startup.timeout.connect(self._check_automatically)
        self._periodic = QTimer(self)
        self._periodic.timeout.connect(self._check_automatically)

        self._exporting = False
        self._export_notice = ""
        self._export_failed = False
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._clear_export_notice)

    def start(self) -> None:
        """Drop installers left from an earlier run, then check at startup if allowed."""
        self._runner.submit(asyncio.to_thread(self._updater.clear))
        self._schedule(self._settings.current, starting=True)

    def stop(self) -> None:
        self._cancel.set()
        self._startup.stop()
        self._periodic.stop()
        self._notice_timer.stop()

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        if self._components is not None:
            self._sync_components()  # the section titles
        self.changed.emit()

    # -- automatic checks --------------------------------------------------------------------
    def _schedule(self, settings: Settings, *, starting: bool = False) -> None:
        self._periodic.stop()
        if not settings.update_check:
            self._startup.stop()
            return
        self._periodic.setInterval(settings.update_check_hours * HOUR_MS)
        self._periodic.start()
        if starting:
            self._startup.start()

    def _settings_changed(self, before: Settings, after: Settings) -> None:
        if (before.update_check, before.update_check_hours) == (
            after.update_check,
            after.update_check_hours,
        ):
            return
        self._schedule(after)
        if after.update_check and not before.update_check:
            self._check_automatically()
        self.changed.emit()

    def _check_automatically(self) -> None:
        # Never in the middle of a download, nor over one the user may still open.
        if self._state in (IDLE, LATEST, AVAILABLE, UNAVAILABLE, FAILED):
            self.checkForUpdates()

    @Property(bool, notify=changed)
    def autoCheck(self) -> bool:
        return self._settings.current.update_check

    @Slot(bool)
    def setAutoCheck(self, enabled: bool) -> None:
        self._store(update_check=enabled)

    @Property(str, notify=changed)
    def checkInterval(self) -> str:
        return str(self._settings.current.update_check_hours)

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def intervalOptions(self) -> list[dict[str, str]]:
        return [{"value": str(h), "label": self.tr(INTERVAL_LABELS[h])} for h in UPDATE_CHECK_HOURS]

    @Slot(str)
    def setCheckInterval(self, hours: str) -> None:
        if hours.isdigit() and int(hours) in UPDATE_CHECK_HOURS:
            self._store(update_check_hours=int(hours))

    def _store(self, **changes: object) -> None:
        try:
            self._settings.update(**changes)
        except SettingsError as error:
            self._fail_text(failure_text(error))

    # -- the update itself -------------------------------------------------------------------
    @Property(str, constant=True)
    def version(self) -> str:
        return self._updater.current

    @Property(str, notify=changed)
    def updateState(self) -> str:
        return self._state

    @Property(str, notify=changed)
    def updateStatus(self) -> str:
        """The version row's caption."""
        state, check = self._state, self._check
        if state == CHECKING:
            return self.tr("正在检查更新…")
        if state == LATEST:
            ago = fmt.ago(self._checked_at, self._clock())
            return self.tr("已是最新版本 · {ago}检查").format(ago=ago)
        version = check.release.version if check and check.release else ""
        if state == AVAILABLE:
            return self.tr("有新版本 {version}").format(version=version)
        if state == UNAVAILABLE:
            text = self.tr("有新版本 {version} · 这个版本没有适合本机的安装包")
            return text.format(version=version)
        if state == DOWNLOADING:
            percent = int(100 * self._received / self._total) if self._total else 0
            return self.tr("正在下载 {percent}%").format(percent=percent)
        if state == VERIFYING:
            return self.tr("正在校验签名…")
        if state == READY:
            return self.tr("{version} 已下载并通过校验").format(version=version)
        if state == FAILED:
            return self._error
        return ""

    @Property(bool, notify=changed)
    def updateFailed(self) -> bool:
        return self._state == FAILED

    @Property(str, notify=changed)
    def updateButton(self) -> str:
        """What the button in the version row does next."""
        state = self._state
        if state == CHECKING:
            return self.tr("正在检查…")
        if state == AVAILABLE:
            return self.tr("下载")
        if state in (DOWNLOADING, VERIFYING):
            return self.tr("取消")
        if state == READY:
            return self.tr("打开安装包…")
        return self.tr("检查更新")

    @Property(bool, notify=changed)
    def updateButtonEnabled(self) -> bool:
        return self._state not in (CHECKING, VERIFYING)

    @Property(str, notify=changed)
    def releaseUrl(self) -> str:
        """The release's page, for its notes, once there is a newer one."""
        check = self._check
        if check is None or check.release is None or not check.newer:
            return ""
        return "" if self._state in (IDLE, CHECKING, LATEST) else check.release.page_url

    @Property(str, notify=changed)
    def installTitle(self) -> str:
        check = self._check
        version = check.release.version if check and check.release else ""
        return self.tr("打开 Solaris {version} 的安装包？").format(version=version)

    @Property(str, notify=changed)
    def installMessage(self) -> str:
        installer = self._check.installer if self._check else None
        if installer and installer.endswith(".exe"):
            return self.tr("安装器会请求管理员权限，并在升级前关闭界面。")
        return self.tr(
            "会在访达中打开磁盘映像，把 Solaris 拖到“应用程序”文件夹完成升级。"
            "替换前先退出界面，隧道继续运行。"
        )

    @Slot()
    def runUpdateAction(self) -> None:
        """The version row's button: check, download, cancel or open, as ``updateButton``
        says."""
        state = self._state
        if state == AVAILABLE:
            self._download()
        elif state == DOWNLOADING:
            self._cancel.set()
        elif state == READY:
            self.installRequested.emit()
        elif state not in (CHECKING, VERIFYING):
            self.checkForUpdates()

    @Slot()
    def checkForUpdates(self) -> None:
        if self._state in (CHECKING, DOWNLOADING, VERIFYING):
            return
        self._set_state(CHECKING)

        def checked(check: Check) -> None:
            self._check = check
            self._checked_at = self._clock()
            if not check.newer:
                self._set_state(LATEST)
            else:
                self._set_state(AVAILABLE if check.installer else UNAVAILABLE)

        self._runner.submit(asyncio.to_thread(self._updater.check), checked, self._failed)

    def _download(self) -> None:
        check = self._check
        if check is None:
            return
        self._cancel = threading.Event()
        self._received = self._total = 0
        self._downloaded = None
        self._set_state(DOWNLOADING)
        cancel = self._cancel

        def downloaded(result: Downloaded) -> None:
            self._downloaded = result
            self._set_state(READY)

        def failed(error: BaseException) -> None:
            if isinstance(error, UpdateError) and error.code == "cancelled":
                self._set_state(AVAILABLE)
            else:
                self._failed(error)

        work = asyncio.to_thread(
            self._updater.download,
            check,
            self._progressed.emit,
            cancel,
            self._verifying.emit,
        )
        self._runner.submit(work, downloaded, failed)

    def _progress(self, received: int, total: int) -> None:
        # Only whole percents redraw the row.
        before = int(100 * self._received / self._total) if self._total else -1
        self._received, self._total = received, total
        if self._state == DOWNLOADING and int(100 * received / total) != before:
            self.changed.emit()

    def _verify_started(self) -> None:
        if self._state == DOWNLOADING:
            self._set_state(VERIFYING)

    @Property(float, notify=changed)
    def downloadProgress(self) -> float:
        return self._received / self._total if self._total else 0.0

    @Slot()
    def confirmInstall(self) -> None:
        """The user confirmed: check the file once more, then let the system open it."""
        downloaded = self._downloaded
        if self._state != READY or downloaded is None:
            return

        def ready(path: Path) -> None:
            if not self._opener(path):
                self._fail_text(self.tr("无法打开安装包：{name}").format(name=path.name))

        def failed(error: BaseException) -> None:
            self._downloaded = None
            self._failed(error)

        self._runner.submit(asyncio.to_thread(self._updater.ready, downloaded), ready, failed)

    def _set_state(self, state: str) -> None:
        self._state = state
        self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self._fail_text(self._failure(error))

    def _fail_text(self, text: str) -> None:
        self._error = text
        self._set_state(FAILED)

    def _failure(self, error: BaseException) -> str:
        if not isinstance(error, UpdateError):
            return failure_text(error)
        text = _FAILURES.get(error.code)
        if text is None:
            return failure_text(error)
        return self.tr(text).format(detail=error.message)

    # -- diagnostics -------------------------------------------------------------------------
    @Property(str, notify=changed)
    def diagnosticsName(self) -> str:
        return diagnostics.archive_name(self._clock())

    @Property(bool, notify=changed)
    def exporting(self) -> bool:
        return self._exporting

    @Property(str, notify=changed)
    def diagnosticsStatus(self) -> str:
        if self._exporting:
            return self.tr("正在导出…")
        if self._export_notice:
            return self._export_notice
        return self.tr("含内核日志，其中有访问过的网站")

    @Property(bool, notify=changed)
    def diagnosticsFailed(self) -> bool:
        return bool(self._export_notice) and self._export_failed

    @Slot(str)
    def exportDiagnostics(self, url: str) -> None:
        """Write the archive to the file a FileDialog chose (a file: URL)."""
        if self._exporting:
            return
        path = Path(QUrl(url).toLocalFile() or url)
        # Read on the Qt thread: the logs page appends to its buffer there.
        logs = tuple(self._logs())
        settings = self._settings.current
        language = current_language()
        now = self._clock()
        self._exporting = True
        self.changed.emit()

        async def export() -> None:
            info, status = await diagnostics.ask_host(self._host)
            snapshot = diagnostics.Snapshot(
                info=info,
                status=status,
                settings=settings,
                profiles=await self._library.current(),
                routing=await self._library.routing(),
                logs=logs,
                language=language,
            )
            await asyncio.to_thread(diagnostics.write_archive, path, snapshot, now)

        def exported(_: object) -> None:
            self._exporting = False
            self._show_export_notice(self.tr("已导出到 {name}").format(name=path.name))

        def failed(error: BaseException) -> None:
            self._exporting = False
            if isinstance(error, OSError):
                detail = error.strerror or str(error)
                text = self.tr("导出失败：{detail}").format(detail=detail)
            else:
                text = failure_text(error)
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

    # -- licenses ----------------------------------------------------------------------------
    def _loaded_components(self) -> tuple[licenses.Component, ...]:
        if self._components is None:
            self._components = licenses.load(self._manifest)
        return self._components

    @Property(QObject, constant=True)
    def components(self) -> KeyedListModel:
        """The third-party components matching the query, as ComponentRow items."""
        return self._component_model

    @Slot(str)
    def setComponentQuery(self, query: str) -> None:
        self._component_query = query
        self._sync_components()

    def _sync_components(self) -> None:
        needle = self._component_query.strip().casefold()
        rows = [
            ComponentRow(
                id=f"{c.kind}:{c.name}",
                name=c.name,
                version=c.version,
                license=c.license,
                category=self.tr(COMPONENT_KINDS.get(c.kind, c.kind)),
            )
            for c in self._loaded_components()
            if not needle or needle in c.name.casefold() or needle in c.license.casefold()
        ]
        self._component_model.sync(rows)
        self.changed.emit()

    @Property(bool, notify=changed)
    def componentsEmpty(self) -> bool:
        return self._component_model.rowCount() == 0

    @Property(str, notify=optionsChanged)
    def componentsSummary(self) -> str:
        return self.tr("%n 个第三方组件", "", len(self._loaded_components()))

    @Slot()
    def openNotices(self) -> None:
        """The full license texts, in the system's text viewer."""
        if self._notices.is_file():
            self._opener(self._notices)
