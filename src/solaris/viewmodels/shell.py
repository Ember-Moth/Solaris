"""Shell ViewModel: the sidebar's pages and the service status shown under them."""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import HostInfo, TunnelHost
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

PAGES = (
    {"id": "overview", "label": QT_TRANSLATE_NOOP("ShellViewModel", "概览"), "icon": "overview"},
    {"id": "proxies", "label": QT_TRANSLATE_NOOP("ShellViewModel", "代理"), "icon": "proxies"},
    {"id": "profiles", "label": QT_TRANSLATE_NOOP("ShellViewModel", "订阅"), "icon": "profiles"},
    {"id": "routing", "label": QT_TRANSLATE_NOOP("ShellViewModel", "分流"), "icon": "routing"},
    {
        "id": "connections",
        "label": QT_TRANSLATE_NOOP("ShellViewModel", "连接"),
        "icon": "connections",
    },
    {"id": "logs", "label": QT_TRANSLATE_NOOP("ShellViewModel", "日志"), "icon": "logs"},
    {"id": "settings", "label": QT_TRANSLATE_NOOP("ShellViewModel", "设置"), "icon": "settings"},
)
# Pages that list what only a running core answers: the sidebar leaves them out until the
# tunnel runs (D27), and the window leaves them for the overview when it stops.
TUNNEL_PAGES = frozenset({"proxies"})
SERVICE_POLL_MS = 10_000


@dataclass(frozen=True)
class PageRow:
    """One page in the sidebar."""

    page: str
    label: str
    iconName: str

    @property
    def id(self) -> str:
        return self.page


# PySide6's stub declares QmlUncreatable without __call__, though instances are
# decorators at runtime; pyright needs this hint on every use.
@QmlElement
@QmlUncreatable("ShellViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ShellViewModel(QObject):
    currentPageChanged = Signal()
    serviceChanged = Signal()
    tunnelChanged = Signal()
    # The tunnel stopped under the current page, which gives way to the overview next; the
    # window moves the keyboard focus off it first.
    leavingPage = Signal()

    def __init__(
        self,
        host: TunnelHost,
        runner: AsyncRunner,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._runner = runner
        self._current_page = "overview"
        self._tunnel_running = False
        # Rows update in place, so the other tabs keep their delegates and the keyboard focus.
        self._pages = KeyedListModel([f.name for f in fields(PageRow)], self)
        self._sync_pages()
        self._info: HostInfo | None = None
        self._service_failed = False
        self._timer = QTimer(self)
        self._timer.setInterval(SERVICE_POLL_MS)
        self._timer.timeout.connect(self.refreshService)

    def start(self) -> None:
        self.refreshService()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self._sync_pages()
        self.serviceChanged.emit()

    @Property(QObject, constant=True)
    def pages(self) -> KeyedListModel:
        """The sidebar's pages as PageRow items, in order; TUNNEL_PAGES only while the
        tunnel runs."""
        return self._pages

    def _sync_pages(self) -> None:
        self._pages.sync(
            [
                PageRow(page["id"], self.tr(page["label"]), page["icon"])
                for page in PAGES
                if self._shows(page["id"])
            ]
        )

    def _shows(self, page: str) -> bool:
        return self._tunnel_running or page not in TUNNEL_PAGES

    def tunnel_running(self, running: bool) -> None:
        """Whether a running core answers: the tunnel is up and the connection switch is
        not on its way (OverviewViewModel). TUNNEL_PAGES show only then."""
        if running == self._tunnel_running:
            return
        self._tunnel_running = running
        if not running and self._current_page in TUNNEL_PAGES:
            self.leavingPage.emit()
            self._current_page = "overview"
            self.currentPageChanged.emit()
        self._sync_pages()
        self.tunnelChanged.emit()

    @Property(bool, notify=tunnelChanged)
    def tunnelRunning(self) -> bool:
        return self._tunnel_running

    @Property(str, notify=currentPageChanged)
    def currentPage(self) -> str:
        return self._current_page

    @Property(int, notify=currentPageChanged)
    def pageIndex(self) -> int:
        return next(i for i, page in enumerate(PAGES) if page["id"] == self._current_page)

    @Slot(str)
    def setPage(self, page: str) -> None:
        """Show ``page``; one the sidebar does not list now stays closed."""
        known = any(p["id"] == page for p in PAGES)
        if page != self._current_page and known and self._shows(page):
            self._current_page = page
            self.currentPageChanged.emit()

    @Property(bool, notify=serviceChanged)
    def serviceAvailable(self) -> bool:
        return self._info is not None

    @Property(str, notify=serviceChanged)
    def serviceText(self) -> str:
        return self.tr("服务运行中") if self._info is not None else self.tr("服务不可用")

    @Property(str, notify=serviceChanged)
    def serviceDetail(self) -> str:
        if self._info is None:
            return self.tr("未能连接到服务") if self._service_failed else ""
        return self.tr("{core} · 协议 v{protocol}").format(
            core=fmt.version_number(self._info.core_version), protocol=self._info.protocol_version
        )

    @Slot()
    def refreshService(self) -> None:
        self._runner.submit(self._host.get_info(), self._apply_info, self._info_failed)

    def _apply_info(self, info: HostInfo) -> None:
        self._info = info
        self._service_failed = False
        self.serviceChanged.emit()

    def _info_failed(self, error: BaseException) -> None:
        self._info = None
        self._service_failed = True
        self.serviceChanged.emit()
