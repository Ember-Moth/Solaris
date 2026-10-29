"""Proxies ViewModel: groups, the nodes of the chosen group, selection and delay tests."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import ProxyGroup, TunnelHost
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP
from mihomo_desktop.models import format as fmt
from mihomo_desktop.models.proxies import SORTS, sort_proxies
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

SORT_LABELS = {
    "config": QT_TRANSLATE_NOOP("ProxiesViewModel", "配置顺序"),
    "name": QT_TRANSLATE_NOOP("ProxiesViewModel", "名称"),
    "delay": QT_TRANSLATE_NOOP("ProxiesViewModel", "延迟"),
}
NO_DELAY_TYPES = ("Direct", "Reject")


@QmlElement
@QmlUncreatable("ProxiesViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ProxiesViewModel(QObject):
    changed = Signal()
    selectionApplied = Signal()  # a selection reached the core; the overview re-reads its node

    def __init__(
        self, host: TunnelHost, runner: AsyncRunner, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._runner = runner
        self._groups: list[ProxyGroup] = []
        self._current = ""
        self._sort = "config"
        self._testing = False
        self._error = ""

    @Slot()
    def refresh(self) -> None:
        self._runner.submit(self._host.list_groups(), self._loaded, self._failed)

    def _loaded(self, groups: list[ProxyGroup]) -> None:
        self._groups = groups
        self._error = ""
        if not any(g.name == self._current for g in groups):
            self._current = groups[0].name if groups else ""
        self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self._testing = False
        self._error = failure_text(error)
        self.changed.emit()

    def _group(self) -> ProxyGroup | None:
        return next((g for g in self._groups if g.name == self._current), None)

    # -- groups ------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def summary(self) -> str:
        return self._error or self.tr("%n 个代理组", "", len(self._groups))

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def groups(self) -> list[dict[str, object]]:
        return [
            {"name": g.name, "kind": g.type, "now": g.now, "selected": g.name == self._current}
            for g in self._groups
        ]

    @Slot(str)
    def selectGroup(self, name: str) -> None:
        if name != self._current:
            self._current = name
            self.changed.emit()

    @Property(str, notify=changed)
    def currentGroup(self) -> str:
        return self._current

    @Property(bool, notify=changed)
    def currentSelectable(self) -> bool:
        group = self._group()
        return bool(group and group.selectable)

    @Property(str, notify=changed)
    def currentHint(self) -> str:
        group = self._group()
        if group is None:
            return ""
        return self.tr("点击切换") if group.selectable else self.tr("由内核按测速结果选择")

    # -- nodes -------------------------------------------------------------------------------
    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def sortOptions(self) -> list[dict[str, str]]:
        return [{"value": s, "label": self.tr(SORT_LABELS[s])} for s in SORTS]

    @Property(str, notify=changed)
    def sort(self) -> str:
        return self._sort

    @Slot(str)
    def setSort(self, sort: str) -> None:
        if sort in SORTS and sort != self._sort:
            self._sort = sort
            self.changed.emit()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def nodes(self) -> list[dict[str, object]]:
        group = self._group()
        if group is None:
            return []
        result: list[dict[str, object]] = []
        for proxy in sort_proxies(group.proxies, self._sort):
            text, level = (
                ("—", "none") if proxy.type in NO_DELAY_TYPES else fmt.latency(proxy.delay_ms)
            )
            result.append(
                {
                    "name": proxy.name,
                    "type": proxy.type,
                    "delay": text,
                    "level": level,
                    "selected": proxy.name == group.now,
                }
            )
        return result

    @Slot(str)
    def selectProxy(self, name: str) -> None:
        group = self._group()
        if group is None or not group.selectable or name == group.now:
            return
        self._runner.submit(self._host.select_proxy(group.name, name), self._selected, self._failed)

    def _selected(self, _: object) -> None:
        self.selectionApplied.emit()
        self.refresh()

    @Property(bool, notify=changed)
    def testing(self) -> bool:
        return self._testing

    @Slot()
    def testCurrentGroup(self) -> None:
        group = self._group()
        if group is None or self._testing:
            return
        self._testing = True
        self.changed.emit()
        self._runner.submit(self._host.health_check(group.name), self._tested, self._failed)

    def _tested(self, _: object) -> None:
        self._testing = False
        self.changed.emit()
        self.selectionApplied.emit()
        self.refresh()
