"""Shell ViewModel: the sidebar's pages and the service status shown under them."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import HostInfo, TunnelHost
from mihomo_desktop.models.profiles import ProfileSource
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

PAGES = (
    {"id": "overview", "label": "概览", "icon": "overview"},
    {"id": "proxies", "label": "代理", "icon": "proxies"},
    {"id": "profiles", "label": "配置", "icon": "profiles"},
    {"id": "connections", "label": "连接", "icon": "connections"},
    {"id": "logs", "label": "日志", "icon": "logs"},
    {"id": "settings", "label": "设置", "icon": "settings"},
)
SERVICE_POLL_MS = 10_000


# PySide6's stub declares QmlUncreatable without __call__, though instances are
# decorators at runtime; pyright needs this hint on every use.
@QmlElement
@QmlUncreatable("ShellViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ShellViewModel(QObject):
    currentPageChanged = Signal()
    serviceChanged = Signal()

    def __init__(
        self,
        host: TunnelHost,
        profiles: ProfileSource,
        runner: AsyncRunner,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._profiles = profiles
        self._runner = runner
        self._current_page = "overview"
        self._info: HostInfo | None = None
        self._service_error = ""
        self._timer = QTimer(self)
        self._timer.setInterval(SERVICE_POLL_MS)
        self._timer.timeout.connect(self.refreshService)

    def start(self) -> None:
        self.refreshService()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def pages(self) -> list[dict[str, str]]:
        return list(PAGES)

    @Property(str, notify=currentPageChanged)
    def currentPage(self) -> str:
        return self._current_page

    @Property(int, notify=currentPageChanged)
    def pageIndex(self) -> int:
        return next(i for i, page in enumerate(PAGES) if page["id"] == self._current_page)

    @Slot(str)
    def setPage(self, page: str) -> None:
        if page != self._current_page and any(p["id"] == page for p in PAGES):
            self._current_page = page
            self.currentPageChanged.emit()

    @Property(str, constant=True)
    def profileName(self) -> str:
        active = self._profiles.active()
        return active.name if active else "未导入配置"

    @Property(bool, notify=serviceChanged)
    def serviceAvailable(self) -> bool:
        return self._info is not None

    @Property(str, notify=serviceChanged)
    def serviceText(self) -> str:
        return "服务运行中" if self._info is not None else "服务不可用"

    @Property(str, notify=serviceChanged)
    def serviceDetail(self) -> str:
        if self._info is None:
            return self._service_error
        return f"{self._info.core_version} · 协议 v{self._info.protocol_version}"

    @Slot()
    def refreshService(self) -> None:
        self._runner.submit(self._host.get_info(), self._apply_info, self._info_failed)

    def _apply_info(self, info: HostInfo) -> None:
        self._info = info
        self._service_error = ""
        self.serviceChanged.emit()

    def _info_failed(self, error: BaseException) -> None:
        self._info = None
        self._service_error = "未能连接到服务"
        self.serviceChanged.emit()
