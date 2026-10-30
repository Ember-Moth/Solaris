"""The plan's groups on the routing page's groups tab (docs/routing.md §4.5): the built-in,
region and custom groups as rows, and the group editor.

A row's menu changes the group's type or hides it; a custom group's removal first names
every place that refers to it (§5.5). The editor shows a custom group's name, type and
conditions on the left, and on the right its members as the core computes them
(PreviewGroup), refreshed as the conditions change.

Members are the nodes of the chosen regions, of the chosen subscriptions or whose names
match, less those the exclusion matches; the picked nodes and the other members always
join. The dialog stays open until the core accepts the change; a refusal shows under the
form.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import GroupPreview, HostError, PoolNode
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import format as fmt
from solaris.models import plans
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import ProfileError
from solaris.models.regions import region_code, region_id, region_name
from solaris.models.routing import DIRECT, REJECT, PlanGroup
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

PREVIEW_MS = 250  # the preview follows the conditions once they stop changing for this long
ALL = "*"  # the 全部节点 choice among the subscriptions
REGIONS_ALL = "region:*"
MATCH_MODES = {
    "keyword": QT_TRANSLATE_NOOP("GroupEditor", "关键字"),
    "regex": QT_TRANSLATE_NOOP("GroupEditor", "正则"),
}
STRATEGIES = ("consistent-hashing", "round-robin", "sticky-sessions")


@dataclass(frozen=True)
class ChoiceRow:
    """A check box of the form: a region, a subscription, a node or another member."""

    id: str
    name: str
    note: str
    picked: bool
    added: bool  # unavailable: a member that would contain the group itself

    @property
    def itemId(self) -> str:  # QML reserves ``id``
        return self.id


@dataclass(frozen=True)
class PreviewRow:
    id: str
    name: str
    source: str
    delay: str
    level: str  # fmt.latency's level, for the colour
    other: bool  # another member, listed before the nodes


@dataclass(frozen=True)
class GroupRow:
    id: str
    name: str
    caption: str
    type: str
    hidden: bool

    @property
    def groupId(self) -> str:
        return self.id


def _roles(row_type: type, id_role: str = "itemId") -> list[str]:
    return [id_role if name == "id" else name for name in row_type.__dataclass_fields__]


def _node_key(source: str, name: str) -> str:
    return f"{source}\n{name}"


def _pattern_error(mode: str, pattern: str) -> bool:
    """Whether a regular expression is broken. Python reads it here; the core's regexp2 has
    the last word when the change is saved."""
    if mode != "regex" or not pattern:
        return False
    try:
        re.compile(pattern)
    except re.error:
        return True
    return False


@QmlElement
@QmlUncreatable("PlanGroups is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class PlanGroups(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed
    removalRequested = Signal()  # a group's removal awaits confirmation

    def __init__(
        self,
        routing: RoutingViewModel,
        runner: AsyncRunner,
        library: ProfileLibrary,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._routing = routing
        self._runner = runner
        self._library = library
        self._removing: str | None = None
        self._builtin = KeyedListModel(_roles(GroupRow, "groupId"), self)
        self._regions = KeyedListModel(_roles(GroupRow, "groupId"), self)
        self._custom = KeyedListModel(_roles(GroupRow, "groupId"), self)
        self.changed.connect(self._sync)
        routing.changed.connect(self.changed)
        self._sync()

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        self.changed.emit()

    @Property(QObject, constant=True)
    def builtin(self) -> KeyedListModel:
        return self._builtin

    @Property(QObject, constant=True)
    def regions(self) -> KeyedListModel:
        return self._regions

    @Property(QObject, constant=True)
    def custom(self) -> KeyedListModel:
        return self._custom

    def put_group(
        self, group: PlanGroup, done: Callable[[], None], failed: Callable[[str], None]
    ) -> None:
        """For the group editor: an edited group keeps its place, a new one comes last. The
        dialog stays open with the core's error when the change is refused."""
        plan = self._routing.plan()
        if plan is None:
            return
        groups = list(plan.groups)
        index = next((i for i, g in enumerate(groups) if g.id == group.id), None)
        if index is None:
            groups.append(group)
        else:
            groups[index] = group
        changed = replace(plan, groups=tuple(groups))
        self._routing.commit(changed, done=done, failed=failed, shown=False)

    # -- removal -----------------------------------------------------------------------------
    def _references(self, group_id: str) -> tuple[list[str], list[str]]:
        """The rule entries that send traffic to the group, and the groups that hold it."""
        plan = self._routing.plan()
        if plan is None:
            return [], []
        rules = [text.rule_name(r) for r in plan.rules if r.target == group_id]
        if plan.fallback == group_id:
            rules.append(self.tr("其他流量"))
        groups = [
            text.group_name(g, g.id)
            for g in plan.groups
            if isinstance(g.conditions, dict) and group_id in g.conditions.get("others", ())
        ]
        return rules, groups

    @Slot(str)
    def requestRemove(self, group_id: str) -> None:
        plan = self._routing.plan()
        group = next((g for g in plan.groups if g.id == group_id), None) if plan else None
        if group is not None and group.kind == "custom":
            self._removing = group_id
            self.changed.emit()
            self.removalRequested.emit()

    @Property(str, notify=changed)
    def removalTitle(self) -> str:
        plan = self._routing.plan()
        group = next((g for g in plan.groups if g.id == self._removing), None) if plan else None
        return self.tr("删除策略组「{name}」？").format(name=group.name) if group else ""

    @Property(str, notify=changed)
    def removalText(self) -> str:
        """Every place that names the group (routing.md §5.5)."""
        if self._removing is None:
            return ""
        rules, groups = self._references(self._removing)
        quoted = self.tr("「{name}」")
        parts = []
        via = plans.warp_exit(self._routing.plan()).via == self._removing
        vpngate_via = plans.vpngate_exit(self._routing.plan()).via == self._removing
        if rules:
            names = "".join(quoted.format(name=n) for n in rules)
            parts.append(
                self.tr("规则{names}以它为目标，删除后改走「节点选择」。").format(names=names)
            )
        if groups:
            names = "".join(quoted.format(name=n) for n in groups)
            parts.append(self.tr("策略组{names}里不再包含它。").format(names=names))
        if via:
            parts.append(self.tr("WARP 出口经由它，删除后改经「节点选择」。"))
        if vpngate_via:
            parts.append(self.tr("VPN Gate 出口经由它，删除后改经「节点选择」。"))
        return "".join(parts) or self.tr("没有规则或策略组引用它。")

    @Slot()
    def confirmRemove(self) -> None:
        group_id, self._removing = self._removing, None
        plan = self._routing.plan()
        group = next((g for g in plan.groups if g.id == group_id), None) if plan else None
        if plan is None or group is None:
            return

        def without(g: PlanGroup) -> PlanGroup:
            if not isinstance(g.conditions, dict) or group.id not in g.conditions.get("others", ()):
                return g
            others = [o for o in g.conditions["others"] if o != group.id]
            conditions = {k: v for k, v in g.conditions.items() if k != "others"}
            return replace(g, conditions={**conditions, "others": others} if others else conditions)

        removed = replace(
            plan,
            groups=tuple(without(g) for g in plan.groups if g.id != group.id),
            rules=tuple(
                replace(r, target=plans.PROXY) if r.target == group.id else r for r in plan.rules
            ),
            fallback=plans.PROXY if plan.fallback == group.id else plan.fallback,
        )
        warp = plans.warp_exit(plan)
        if warp.via == group.id:
            removed = plans.with_warp_exit(removed, replace(warp, via=plans.PROXY))
        vpngate = plans.vpngate_exit(plan)
        if vpngate.via == group.id:
            removed = plans.with_vpngate_exit(removed, replace(vpngate, via=plans.PROXY))
        status = self.tr("已删除「{name}」").format(name=group.name)
        action = self.tr("撤销删除「{name}」").format(name=group.name)
        self._routing.commit(removed, undo=(status, action, plan))

    # -- a row's menu ------------------------------------------------------------------------
    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def typeOptions(self) -> list[dict[str, str]]:
        return [{"value": t, "label": text.type_label(t)} for t in text.GROUP_TYPES]

    @Slot(str, str)
    def setType(self, group_id: str, group_type: str) -> None:
        if group_type in plans.GROUP_TYPES:
            self._patch(group_id, lambda g: replace(g, type=group_type))

    @Slot(str, bool)
    def setHidden(self, group_id: str, hidden: bool) -> None:
        self._patch(group_id, lambda g: replace(g, hidden=hidden))

    def _patch(self, group_id: str, change: Callable[[PlanGroup], PlanGroup]) -> None:
        plan = self._routing.plan()
        if plan is None:
            return
        groups = list(plan.groups)
        index = next((i for i, g in enumerate(groups) if g.id == group_id), None)
        if index is None:
            kind = "region" if region_code(group_id) else "builtin"
            default = "select" if group_id == plans.PROXY else "url-test"
            groups.append(change(PlanGroup(group_id, kind, default)))
        else:
            groups[index] = change(groups[index])
        self._routing.commit(replace(plan, groups=tuple(groups)))

    # -- rows --------------------------------------------------------------------------------
    def _sync(self) -> None:
        plan = self._routing.plan()
        report = self._routing.report()
        settings = {g.id: g for g in plan.groups} if plan else {}
        regions = text.regions_with_nodes(report)
        counts = {g.id: g.node_count for g in report.groups} if report else {}
        builtin = []
        for group_id in (plans.PROXY, plans.AUTO):
            group = settings.get(group_id)
            group_type = (
                group.type if group else ("select" if group_id == plans.PROXY else "url-test")
            )
            if group_id == plans.PROXY:
                members = self.tr("自动选择、{regions}、直连").format(
                    regions=self.tr("%n 个地区组", "", len(regions))
                )
            else:
                members = self.tr("%n 个节点", "", counts.get(group_id, 0))
            builtin.append(self._row(group_id, group, group_type, members))
        self._builtin.sync(builtin)
        region_rows = []
        for code, count in regions:
            group_id = f"region:{code}"
            group = settings.get(group_id)
            members = self.tr("%n 个节点", "", count)
            region_rows.append(
                self._row(group_id, group, group.type if group else "url-test", members)
            )
        self._regions.sync(region_rows)
        custom_rows = []
        for group in plan.groups if plan else ():
            if group.kind != "custom":
                continue
            others = (
                group.conditions.get("others", ()) if isinstance(group.conditions, dict) else ()
            )
            parts = []
            if counts.get(group.id):
                parts.append(self.tr("%n 个节点", "", counts[group.id]))
            named = [o for o in others if o not in (DIRECT, REJECT)]
            if named:
                parts.append(self.tr("%n 个其他成员", "", len(named)))
            parts += [text.target_label(o, plan) for o in others if o in (DIRECT, REJECT)]
            members = self.tr("、").join(parts) if parts else self.tr("没有成员")
            custom_rows.append(self._row(group.id, group, group.type, members))
        self._custom.sync(custom_rows)

    def _row(
        self, group_id: str, group: PlanGroup | None, group_type: str, members: str
    ) -> GroupRow:
        hidden = bool(group and group.hidden)
        parts = [text.type_label(group_type), members]
        caption = " · ".join(parts)
        if region_code(group_id) is not None:
            prefix = self.tr("自动 · 已隐藏") if hidden else self.tr("自动")
            caption = f"{prefix} · {caption}"
        elif hidden:
            caption = self.tr("已隐藏 · {caption}").format(caption=caption)
        code = region_code(group_id)
        name = region_name(code) if code else text.group_name(group, group_id)
        return GroupRow(group_id, name, caption, group_type, hidden)


@QmlElement
@QmlUncreatable("GroupEditor is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class GroupEditor(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed
    previewChanged = Signal()
    opened = Signal()  # the view opens the dialog
    saved = Signal()  # the change is stored; the view closes the dialog

    def __init__(
        self,
        routing: RoutingViewModel,
        runner: AsyncRunner,
        library: ProfileLibrary,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._routing = routing
        self._runner = runner
        self._library = library
        self._regions = KeyedListModel(_roles(ChoiceRow), self)
        self._sources = KeyedListModel(_roles(ChoiceRow), self)
        self._nodes = KeyedListModel(_roles(ChoiceRow), self)
        self._others = KeyedListModel(_roles(ChoiceRow), self)
        self._preview_rows = KeyedListModel(list(PreviewRow.__dataclass_fields__), self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_MS)
        self._timer.timeout.connect(self._refresh_preview)
        self._preview: GroupPreview | None = None
        self._preview_error = ""
        self._previewing = False
        self._pending = False  # the conditions changed while a preview ran
        self._delays: dict[str, int | None] = {}
        self._reset(None, copy=False)
        self.changed.connect(self._sync_rows)
        self.previewChanged.connect(self._sync_preview)

    def _reset(self, group: PlanGroup | None, *, copy: bool) -> None:
        self._editing = group if group is not None and not copy else None
        self._id = self._editing.id if self._editing else plans.new_id("g")
        conditions: dict[str, Any] = dict(group.conditions) if group else {}
        options: dict[str, Any] = dict(group.options) if group else {}
        if group is None:
            self._name = ""
        elif copy:
            self._name = self.tr("{name} 副本").format(name=text.group_name(group, group.id))
        else:
            self._name = group.name
        self._type = group.type if group else "select"
        self._hidden = bool(group.hidden) if group and not copy else False
        self._all = bool(conditions.get("all"))
        self._region_codes = [str(c) for c in conditions.get("regions", ())]
        self._source_ids = [str(s) for s in conditions.get("sources", ())]
        name = conditions.get("name") or {}
        self._name_mode = str(name.get("mode", "keyword"))
        self._name_pattern = str(name.get("pattern", ""))
        exclude = conditions.get("exclude") or {}
        self._exclude_mode = str(exclude.get("mode", "keyword"))
        self._exclude_pattern = str(exclude.get("pattern", ""))
        self._picked = [
            _node_key(str(n.get("source", "")), str(n.get("name", "")))
            for n in conditions.get("nodes", ())
        ]
        self._other_ids = [str(o) for o in conditions.get("others", ())]
        self._url = str(options.get("url", ""))
        self._interval = str(options.get("interval", ""))
        self._tolerance = str(options.get("tolerance", ""))
        self._strategy = str(options.get("strategy", STRATEGIES[0]))
        # Sections with something set start open (DESIGN.md "可折叠区").
        self._more_open = self._more_count() > 0
        self._advanced_open = bool(options)
        self._manual_open = False
        self._manual_query = ""
        self._saving = False
        self._error = ""
        self._preview = None
        self._preview_error = ""

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        self.changed.emit()
        self.previewChanged.emit()

    # -- opening -----------------------------------------------------------------------------
    @Slot()
    def openAdd(self) -> None:
        self._open(None, copy=False)

    @Slot(str)
    def openEdit(self, group_id: str) -> None:
        group = self._group(group_id)
        if group is not None and group.kind == "custom":
            self._open(group, copy=False)

    @Slot(str)
    def openCopy(self, group_id: str) -> None:
        """A new custom group with the conditions of ``group_id``; a region's are its region."""
        group = self._group(group_id) or self._default_group(group_id)
        code = region_code(group_id)
        if code is not None:
            group_type = group.type if group else "url-test"
            group = PlanGroup(group_id, "region", group_type, "", {"regions": [code]})
        if group is not None:
            self._open(group, copy=True)

    def _group(self, group_id: str) -> PlanGroup | None:
        plan = self._routing.plan()
        return next((g for g in plan.groups if g.id == group_id), None) if plan else None

    def _default_group(self, group_id: str) -> PlanGroup | None:
        """A built-in group the plan keeps at its defaults (routing.md §6)."""
        if group_id == plans.PROXY:
            return PlanGroup(
                group_id, "builtin", "select", "", {"others": [plans.AUTO, REGIONS_ALL, DIRECT]}
            )
        if group_id == plans.AUTO:
            return PlanGroup(group_id, "builtin", "url-test", "", {"all": True})
        return None

    def _open(self, group: PlanGroup | None, *, copy: bool) -> None:
        self._reset(group, copy=copy)
        self._delays = {}
        self.changed.emit()
        self.previewChanged.emit()
        self.opened.emit()
        self._refresh_preview()

        def delays_read(delays: dict[str, int | None]) -> None:
            self._delays = delays
            self.previewChanged.emit()

        self._runner.submit(self._library.plans.node_delays(), delays_read, lambda _: None)

    # -- the form ----------------------------------------------------------------------------
    @Property(str, notify=changed)
    def title(self) -> str:
        if self._editing is not None:
            return self.tr("编辑策略组「{name}」").format(name=self._editing.name)
        return self.tr("新建策略组")

    @Property(str, notify=changed)
    def confirmLabel(self) -> str:
        return self.tr("保存") if self._editing is not None else self.tr("添加")

    @Property(str, notify=changed)
    def name(self) -> str:
        return self._name

    @Slot(str)
    def setName(self, name: str) -> None:
        if name != self._name:
            self._name = name
            self._changed()

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def typeOptions(self) -> list[dict[str, str]]:
        return [{"value": t, "label": text.type_label(t)} for t in plans.GROUP_TYPES]

    @Property(str, notify=changed)
    def type(self) -> str:
        return self._type

    @Slot(str)
    def setType(self, group_type: str) -> None:
        if group_type in plans.GROUP_TYPES and group_type != self._type:
            self._type = group_type
            self.changed.emit()

    @Property(QObject, constant=True)
    def regions(self) -> KeyedListModel:
        return self._regions

    @Property(bool, notify=changed)
    def hasRegions(self) -> bool:
        return bool(self._region_codes or text.regions_with_nodes(self._routing.report()))

    @Slot(str)
    def toggleRegion(self, code: str) -> None:
        self._toggle(self._region_codes, code)

    # -- 更多条件 ----------------------------------------------------------------------------
    def _more_count(self) -> int:
        return sum(
            (
                bool(self._source_ids) or self._all,
                bool(self._name_pattern),
                bool(self._picked),
                bool(self._other_ids),
                bool(self._exclude_pattern),
            )
        )

    @Property(str, notify=changed)
    def moreLabel(self) -> str:
        count = self._more_count()
        if count:
            return self.tr("更多条件（已设置 %n 项）", "", count)
        return self.tr("更多条件")

    @Property(bool, notify=changed)
    def moreOpen(self) -> bool:
        return self._more_open

    @Slot(bool)
    def setMoreOpen(self, open_: bool) -> None:
        self._more_open = open_
        self.changed.emit()

    @Property(QObject, constant=True)
    def sources(self) -> KeyedListModel:
        return self._sources

    @Slot(str)
    def toggleSource(self, source_id: str) -> None:
        if source_id == ALL:
            self._all = not self._all
            self._changed()
        else:
            self._toggle(self._source_ids, source_id)

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def matchModeOptions(self) -> list[dict[str, str]]:
        return [{"value": mode, "label": self.tr(label)} for mode, label in MATCH_MODES.items()]

    @Property(str, notify=changed)
    def nameMode(self) -> str:
        return self._name_mode

    @Property(str, notify=changed)
    def namePattern(self) -> str:
        return self._name_pattern

    @Slot(str, str)
    def setNameMatch(self, mode: str, pattern: str) -> None:
        if (mode, pattern) != (self._name_mode, self._name_pattern) and mode in MATCH_MODES:
            self._name_mode, self._name_pattern = mode, pattern
            self._changed()

    @Property(bool, notify=changed)
    def nameInvalid(self) -> bool:
        return _pattern_error(self._name_mode, self._name_pattern)

    @Property(str, notify=changed)
    def excludeMode(self) -> str:
        return self._exclude_mode

    @Property(str, notify=changed)
    def excludePattern(self) -> str:
        return self._exclude_pattern

    @Slot(str, str)
    def setExclude(self, mode: str, pattern: str) -> None:
        if (mode, pattern) != (self._exclude_mode, self._exclude_pattern) and mode in MATCH_MODES:
            self._exclude_mode, self._exclude_pattern = mode, pattern
            self._changed()

    @Property(bool, notify=changed)
    def excludeInvalid(self) -> bool:
        return _pattern_error(self._exclude_mode, self._exclude_pattern)

    @Property(QObject, constant=True)
    def nodes(self) -> KeyedListModel:
        """The pool's nodes to pick from, filtered by the manual search."""
        return self._nodes

    @Property(str, notify=changed)
    def manualSummary(self) -> str:
        if not self._picked:
            return self.tr("未挑选")
        return self.tr("已挑选 %n 个节点", "", len(self._picked))

    @Property(bool, notify=changed)
    def manualOpen(self) -> bool:
        return self._manual_open

    @Slot(bool)
    def setManualOpen(self, open_: bool) -> None:
        self._manual_open = open_
        self.changed.emit()

    @Slot(str)
    def setManualQuery(self, query: str) -> None:
        self._manual_query = query
        self.changed.emit()

    @Property(bool, notify=changed)
    def nodesEmpty(self) -> bool:
        query = self._manual_query.strip().lower()
        return not any(not query or query in node.name.lower() for node in self._pool())

    @Slot(str)
    def toggleNode(self, key: str) -> None:
        self._toggle(self._picked, key)

    @Property(QObject, constant=True)
    def others(self) -> KeyedListModel:
        return self._others

    @Slot(str)
    def toggleOther(self, other: str) -> None:
        self._toggle(self._other_ids, other)

    # -- 高级 --------------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def hasAdvanced(self) -> bool:
        """Only groups that test delays have advanced settings."""
        return self._type != "select"

    @Property(bool, notify=changed)
    def advancedOpen(self) -> bool:
        return self._advanced_open

    @Slot(bool)
    def setAdvancedOpen(self, open_: bool) -> None:
        self._advanced_open = open_
        self.changed.emit()

    @Property(str, notify=changed)
    def url(self) -> str:
        return self._url

    @Property(str, notify=changed)
    def interval(self) -> str:
        return self._interval

    @Property(str, notify=changed)
    def tolerance(self) -> str:
        return self._tolerance

    @Property(str, notify=changed)
    def strategy(self) -> str:
        return self._strategy

    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def strategyOptions(self) -> list[dict[str, str]]:
        return [{"value": s, "label": s} for s in STRATEGIES]

    @Slot(str, str)
    def setOption(self, key: str, value: str) -> None:
        if key not in ("url", "interval", "tolerance", "strategy"):
            return
        setattr(self, f"_{key}", value.strip())
        self.changed.emit()

    # -- the preview -------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def preview(self) -> KeyedListModel:
        return self._preview_rows

    @Property(str, notify=previewChanged)
    def previewTitle(self) -> str:
        if self._preview is None:
            return ""
        nodes = self.tr("%n 个节点", "", len(self._preview.nodes))
        if not self._preview.others:
            return nodes
        others = self.tr("%n 个其他成员", "", len(self._preview.others))
        return self.tr("{nodes}、{others}").format(nodes=nodes, others=others)

    @Property(bool, notify=previewChanged)
    def previewEmpty(self) -> bool:
        preview = self._preview
        return preview is not None and not preview.nodes and not preview.others

    @Property(str, notify=previewChanged)
    def previewError(self) -> str:
        return self._preview_error

    @Property(str, notify=changed)
    def coreNote(self) -> str:
        """Where the preview and the pool's nodes come from while no core answers."""
        return "" if self._routing.core_available() else self.tr("连接后显示")

    def core_changed(self) -> None:
        """A core started or stopped answering: the note at once, the preview once it can."""
        self._changed()

    def _changed(self) -> None:
        """A condition changed: the form at once, the preview once it settles."""
        self.changed.emit()
        self._timer.start()

    def _refresh_preview(self) -> None:
        plan = self._routing.plan()
        if plan is None or not self._routing.core_available():
            return
        if self._previewing:
            self._pending = True
            return
        self._previewing = True
        self._pending = False

        def shown(preview: GroupPreview) -> None:
            self._previewing = False
            self._preview, self._preview_error = preview, ""
            self.previewChanged.emit()
            if self._pending:
                self._refresh_preview()

        def failed(error: BaseException) -> None:
            self._previewing = False
            if isinstance(error, HostError | ProfileError):
                self._preview_error = failure_text(error)
                self.previewChanged.emit()
            if self._pending:
                self._refresh_preview()

        # The name counts only when the group is saved; the preview should not wait for one.
        draft = replace(self._draft(), name=self._id)
        self._runner.submit(self._library.plans.preview_group(plan, draft), shown, failed)

    def _sync_preview(self) -> None:
        preview = self._preview
        if preview is None:
            self._preview_rows.sync([])
            return
        plan = self._routing.plan()
        names = self._routing.profile_names()
        rows = [
            PreviewRow(
                f"other:{other}",
                self.tr("全部地区组") if other == REGIONS_ALL else text.target_label(other, plan),
                "" if other in (DIRECT, REJECT) else self.tr("策略组"),
                "",
                "none",
                True,
            )
            for other in preview.others
        ]
        for node in preview.nodes:
            delay, level = fmt.latency(self._delays.get(node.name))
            source = names.get(node.source_id, "")
            rows.append(PreviewRow(f"node:{node.name}", node.name, source, delay, level, False))
        self._preview_rows.sync(rows)

    # -- saving ------------------------------------------------------------------------------
    def _conditions(self) -> dict[str, Any]:
        conditions: dict[str, Any] = {}
        if self._all:
            conditions["all"] = True
        if self._region_codes:
            conditions["regions"] = list(self._region_codes)
        if self._source_ids:
            conditions["sources"] = list(self._source_ids)
        if self._name_pattern:
            conditions["name"] = {"mode": self._name_mode, "pattern": self._name_pattern}
        if self._exclude_pattern:
            conditions["exclude"] = {"mode": self._exclude_mode, "pattern": self._exclude_pattern}
        if self._picked:
            conditions["nodes"] = [
                {"source": source, "name": name}
                for source, _, name in (key.partition("\n") for key in self._picked)
            ]
        if self._other_ids:
            conditions["others"] = list(self._other_ids)
        return conditions

    def _options(self) -> dict[str, Any]:
        if self._type == "select":
            return {}
        options: dict[str, Any] = {}
        if self._url:
            options["url"] = self._url
        if self._interval.isdigit() and int(self._interval) > 0:
            options["interval"] = int(self._interval)
        if self._type == "url-test" and self._tolerance.isdigit():
            options["tolerance"] = int(self._tolerance)
        if self._type == "load-balance":
            options["strategy"] = self._strategy
        return options

    def _draft(self) -> PlanGroup:
        return PlanGroup(
            self._id,
            "custom",
            self._type,
            self._name.strip(),
            self._conditions(),
            self._options(),
            self._hidden,
        )

    @Property(bool, notify=changed)
    def canSave(self) -> bool:
        return (
            not self._saving
            and bool(self._name.strip())
            and not _pattern_error(self._name_mode, self._name_pattern)
            and not _pattern_error(self._exclude_mode, self._exclude_pattern)
        )

    @Property(bool, notify=changed)
    def saving(self) -> bool:
        return self._saving

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def save(self) -> None:
        if not self.canSave:
            return
        self._saving = True
        self._error = ""
        self.changed.emit()

        def done() -> None:
            self._saving = False
            self.changed.emit()
            self.saved.emit()

        def failed(message: str) -> None:
            self._saving = False
            self._error = message
            self.changed.emit()

        self._routing.plan_groups().put_group(self._draft(), done, failed)

    # -- rows --------------------------------------------------------------------------------
    def _toggle(self, chosen: list[str], value: str) -> None:
        if value in chosen:
            chosen.remove(value)
        else:
            chosen.append(value)
        self._changed()

    def _pool(self) -> tuple[PoolNode, ...]:
        report = self._routing.report()
        return report.nodes if report else ()

    def _sync_rows(self) -> None:
        pool = self._pool()
        counts: dict[str, int] = {}
        for node in pool:
            counts[node.region] = counts.get(node.region, 0) + 1
        # Regions with nodes, in table order; a chosen one without nodes stays listed.
        regions = [code for code, _ in text.regions_with_nodes(self._routing.report())]
        regions += [code for code in self._region_codes if code not in regions]
        self._regions.sync(
            [
                ChoiceRow(
                    code,
                    region_name(code),
                    str(counts.get(code, 0)),
                    code in self._region_codes,
                    False,
                )
                for code in regions
            ]
        )
        names = self._routing.profile_names()
        by_source: dict[str, int] = {}
        for node in pool:
            by_source[node.source_id] = by_source.get(node.source_id, 0) + 1
        sources = [ChoiceRow(ALL, self.tr("全部节点"), str(len(pool)), self._all, False)]
        sources += [
            ChoiceRow(
                profile_id,
                name,
                str(by_source.get(profile_id, 0)),
                profile_id in self._source_ids,
                False,
            )
            for profile_id, name in names.items()
            if profile_id in by_source or profile_id in self._source_ids
        ]
        self._sources.sync(sources)
        query = self._manual_query.strip().lower()
        nodes = [
            ChoiceRow(
                _node_key(node.source_id, node.original_name),
                node.name,
                names.get(node.source_id, ""),
                _node_key(node.source_id, node.original_name) in self._picked,
                False,
            )
            for node in pool
            if not query or query in node.name.lower()
        ]
        self._nodes.sync(nodes)
        self._others.sync(self._other_rows())

    def _other_rows(self) -> list[ChoiceRow]:
        plan = self._routing.plan()
        groups = plan.groups if plan else ()
        choices = [plans.PROXY, plans.AUTO, REGIONS_ALL]
        choices += [region_id(code) for code, _ in text.regions_with_nodes(self._routing.report())]
        choices += [g.id for g in groups if g.kind == "custom"]
        choices += [DIRECT, REJECT]
        choices += [o for o in self._other_ids if o not in choices]
        rows = []
        for choice in choices:
            if choice == self._id:
                continue
            label = (
                self.tr("全部地区组") if choice == REGIONS_ALL else text.target_label(choice, plan)
            )
            rows.append(
                ChoiceRow(
                    choice, label, "", choice in self._other_ids, self._reaches(choice, self._id)
                )
            )
        return rows

    def _reaches(self, start: str, target: str) -> bool:
        """Whether the group ``start`` holds ``target``, directly or through other groups, so
        making it a member of ``target`` would loop."""
        plan = self._routing.plan()
        if plan is None or region_code(start) is not None or start in (DIRECT, REJECT, REGIONS_ALL):
            return False
        groups = {g.id: g for g in plan.groups}
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            group = groups.get(current) or self._default_group(current)
            if group is not None and isinstance(group.conditions, dict):
                stack.extend(str(o) for o in group.conditions.get("others", ()))
        return False
