"""Explicit macOS extension maintenance, separate from ordinary connect/disconnect."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import HostError, TunnelHost
from solaris.models.library import ProfileLibrary
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlUncreatable("SystemExtensionViewModel is created in Python.")  # pyright: ignore[reportCallIssue]
class SystemExtensionViewModel(QObject):
    changed = Signal()
    busyChanged = Signal(bool)
    finished = Signal()

    def __init__(
        self,
        host: TunnelHost,
        library: ProfileLibrary,
        runner: AsyncRunner,
        can_begin: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._library = library
        self._runner = runner
        self._can_begin = can_begin
        self._busy = False
        self._state = "ready"
        self._failure: BaseException | None = None
        self._was_connected = False

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._host.can_manage_extension

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(str, notify=changed)
    def detail(self) -> str:
        match self._state:
            case "working":
                return self.tr("正在停用…")
            case "done":
                return self.tr("停用已完成，下次连接时会重新启用")
            case "pending":
                return self.tr("系统尚未完成停用，请按系统提示操作后重试")
            case "busy":
                return self.tr("系统正在处理其他扩展请求，请完成后重试")
            case "cancelled":
                return self.tr("停用已取消，连接保持断开")
            case "connection_busy":
                return self.tr("请等待当前连接操作完成后再停用")
            case "failed":
                if isinstance(self._failure, HostError) and self._failure.code == "timeout":
                    return self.tr("停用超时，请重试")
                return self.tr("未能停用扩展：{detail}").format(
                    detail=failure_text(self._failure or RuntimeError())
                )
        return self.tr("卸载应用前可停用扩展，订阅和设置会保留")

    @Slot()
    def deactivate(self) -> None:
        if not self._host.can_manage_extension or self._busy:
            return
        if not self._can_begin():
            self._state = "connection_busy"
            self.changed.emit()
            return
        self._busy, self._state, self._failure = True, "working", None
        self.busyChanged.emit(True)
        self.changed.emit()
        self._runner.submit(self._library.tunnel.deactivate_extension(), self._done, self._failed)

    def _done(self, _: object) -> None:
        self._finish("done")

    def _failed(self, error: BaseException) -> None:
        self._failure = error
        state = (
            {
                "extension_deactivation_pending": "pending",
                "extension_busy": "busy",
                "cancelled": "cancelled",
            }.get(error.code, "failed")
            if isinstance(error, HostError)
            else "failed"
        )
        self._finish(state)

    def _finish(self, state: str) -> None:
        self._busy, self._state = False, state
        self.busyChanged.emit(False)
        self.changed.emit()
        self.finished.emit()

    def tunnel_changed(self, connected: bool) -> None:
        # Only a new connection clears the last result: an old running snapshot may
        # still be visible while the maintenance completion callback is delivered.
        if connected and not self._was_connected and not self._busy and self._state != "ready":
            self._state, self._failure = "ready", None
            self.changed.emit()
        self._was_connected = connected

    def retranslate(self) -> None:
        self.changed.emit()
