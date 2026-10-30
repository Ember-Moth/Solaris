"""Proxies ViewModel: groups, the nodes of the chosen group, selection and delay tests.

When the tunnel routes with a plan, the region groups fold into a section of their own and
each node names the subscription it came from (docs/routing.md §4.6)."""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import ProxyGroup, TunnelHost
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP
from mihomo_desktop.models import format as fmt
from mihomo_desktop.models.proxies import SORTS, sort_proxies
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.common.keyed_list import KeyedListModel
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

SORT_LABELS = {
    "config": QT_TRANSLATE_NOOP("ProxiesViewModel", "配置顺序"),
    "name": QT_TRANSLATE_NOOP("ProxiesViewModel", "名称"),
    "delay": QT_TRANSLATE_NOOP("ProxiesViewModel", "延迟"),
}
NO_DELAY_TYPES = ("Direct", "Reject")


REGIONS_ROW = "\x00regions"  # the row that folds the region groups; no group has this name


@dataclass(frozen=True)
class GroupRow:
    """One proxy group in the list of groups; names are unique, so the name is the id. The
    row that folds the region groups is a header, with their count in ``now``."""

    name: str
    kind: str
    now: str
    selected: bool
    header: bool = False
    region: bool = False  # a region group, indented under the header
    expanded: bool = False  # the header's state

    @property
    def id(self) -> str:
        return REGIONS_ROW if self.header else self.name


@dataclass(frozen=True)
class NodeRow:
    """One node of the current group."""

    name: str
    type: str
    delay: str
    level: str
    selected: bool
    source: str = ""  # the subscription of a pool node

    @property
    def id(self) -> str:
        return self.name


@QmlElement
@QmlUncreatable("ProxiesViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ProxiesViewModel(QObject):
    changed = Signal()
    sortOptionsChanged = Signal()  # the UI language changed
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
        self._sources: dict[str, str] = {}  # pool node -> subscription name
        self._regions: frozenset[str] = frozenset()  # names of the region groups
        self._regions_expanded = False
        # Rows update in place, so the lists keep their delegates and the keyboard focus.
        self._group_model = KeyedListModel([f.name for f in fields(GroupRow)], self)
        self._node_model = KeyedListModel([f.name for f in fields(NodeRow)], self)
        self.changed.connect(self._sync_rows)

    @Slot()
    def refresh(self) -> None:
        self._runner.submit(self._host.list_groups(), self._loaded, self._failed)

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self.sortOptionsChanged.emit()
        self.changed.emit()

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

    @Property(QObject, constant=True)
    def groups(self) -> KeyedListModel:
        """The groups as GroupRow items."""
        return self._group_model

    def _sync_rows(self) -> None:
        rows: list[GroupRow] = []
        regions = [g for g in self._groups if g.name in self._regions]
        expanded = self._regions_expanded or any(g.name == self._current for g in regions)
        for group in self._groups:
            if group.name not in self._regions:
                rows.append(
                    GroupRow(group.name, group.type, group.now, group.name == self._current)
                )
                continue
            if group is regions[0]:
                count = self.tr("%n 个", "", len(regions))
                rows.append(
                    GroupRow(self.tr("地区"), "", count, False, header=True, expanded=expanded)
                )
            if expanded:
                rows.append(
                    GroupRow(
                        group.name, group.type, group.now, group.name == self._current, region=True
                    )
                )
        self._group_model.sync(rows)
        self._node_model.sync(self._nodes())

    def set_pool(self, sources: dict[str, str], regions: frozenset[str]) -> None:
        """The pool of the plan the tunnel routes with: each node's subscription, and which
        groups are region groups. Empty when it runs a profile's own rules."""
        if (sources, regions) != (self._sources, self._regions):
            self._sources, self._regions = sources, regions
            self.changed.emit()

    @Property(bool, notify=changed)
    def showSource(self) -> bool:
        return bool(self._sources)

    @Slot()
    def toggleRegions(self) -> None:
        if self._current in self._regions:
            return  # the chosen group keeps its section open
        self._regions_expanded = not self._regions_expanded
        self.changed.emit()

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
    @Property("QVariantList", notify=sortOptionsChanged)  # pyright: ignore[reportArgumentType]
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

    @Property(QObject, constant=True)
    def nodes(self) -> KeyedListModel:
        """The current group's nodes, in the chosen order, as NodeRow items."""
        return self._node_model

    def _nodes(self) -> list[NodeRow]:
        group = self._group()
        if group is None:
            return []
        result: list[NodeRow] = []
        for proxy in sort_proxies(group.proxies, self._sort):
            text, level = (
                ("—", "none") if proxy.type in NO_DELAY_TYPES else fmt.latency(proxy.delay_ms)
            )
            selected = proxy.name == group.now
            source = self._sources.get(proxy.name, "")
            result.append(NodeRow(proxy.name, proxy.type, text, level, selected, source))
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
