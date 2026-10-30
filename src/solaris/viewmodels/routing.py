"""Routing ViewModel: the routing page (docs/routing.md §4.3). The routing mode, the profile
whose own rules are used, and the user's plans with their rules and groups.

Every confirmed change goes to the library at once, which validates it before storing it and
reloads a running tunnel (§5.4). The page shows the change straight away; a rejected one
puts the stored plan back and says why in the header. Moves, by dragging or from the menu,
wait about a second for the next one and go as one change. A removed rule can be brought
back for eight seconds, or until the next change.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import CompositionReport, HostError
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import plans
from solaris.models.library import ProfileLibrary, ProfileRules, Routing
from solaris.models.profiles import ProfileError, ProfileList
from solaris.models.regions import region_code, region_name
from solaris.models.routing import (
    DEFAULT_INFO_PATTERN,
    MODE_PLAN,
    MODE_PROFILE,
    Plan,
    PlanGroup,
    PlanRule,
)
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text
from solaris.viewmodels.rule_editor import RuleEditor

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

MODE_LABELS = {
    MODE_PROFILE: QT_TRANSLATE_NOOP("RoutingViewModel", "订阅自带"),
    MODE_PLAN: QT_TRANSLATE_NOOP("RoutingViewModel", "我的方案"),
}
MERGE_MS = 1000  # moves that follow each other within this go as one change
UNDO_MS = 8000


@dataclass(frozen=True)
class RuleRow:
    id: str
    name: str
    caption: str
    mono: bool
    target: str
    targetId: str
    active: bool  # enabled; ``enabled`` is an Item's own property in QML
    warning: str  # the target is unavailable for now; empty otherwise
    first: bool
    last: bool
    editable: bool

    @property
    def ruleId(self) -> str:  # QML reserves ``id``
        return self.id


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


@dataclass(frozen=True)
class SourceGroupRow:
    """A group of the profile in use, as the core runs it."""

    id: str
    name: str
    caption: str


@dataclass(frozen=True)
class SourceRuleRow:
    id: str
    match: str
    target: str


def _fields(row_type: type, id_role: str = "") -> list[str]:
    """The roles of a row type: its fields, with ``id`` under another name for QML."""
    names = list(row_type.__dataclass_fields__)
    return [id_role if name == "id" and id_role else name for name in names]


@QmlElement
@QmlUncreatable("RoutingViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class RoutingViewModel(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed
    routingChanged = Signal()  # the mode, the profile in use or the pool may have changed
    applied = Signal()  # the tunnel may run another configuration now
    planSaved = Signal()  # a plan was created or renamed; the view closes its dialog
    planRemovalRequested = Signal()  # a plan's removal awaits confirmation
    infoPatternSaved = Signal()

    def __init__(
        self,
        library: ProfileLibrary,
        runner: AsyncRunner,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._runner = runner
        self._routing: Routing = library.routing_snapshot()
        self._list: ProfileList = library.snapshot()
        self._draft: Plan | None = None  # shown, not stored yet
        self._error = ""
        self._busy = False
        self._undo: tuple[str, str, Plan] | None = None  # status text, button name, plan
        self._profile_rules: ProfileRules | None = None
        self._rule_query = ""
        self._plan_error = ""
        self._info_error = ""
        self._removing_plan: Plan | None = None
        self._merge = QTimer(self)
        self._merge.setSingleShot(True)
        self._merge.setInterval(MERGE_MS)
        self._merge.timeout.connect(self._store_moves)
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(UNDO_MS)
        self._undo_timer.timeout.connect(self._expire_undo)
        self._rules = KeyedListModel(_fields(RuleRow, "ruleId"), self)
        self._builtin_groups = KeyedListModel(_fields(GroupRow, "groupId"), self)
        self._region_groups = KeyedListModel(_fields(GroupRow, "groupId"), self)
        self._source_groups = KeyedListModel(_fields(SourceGroupRow), self)
        self._source_rules = KeyedListModel(_fields(SourceRuleRow), self)
        self._editor = RuleEditor(self, runner, library, self)
        self.changed.connect(self._sync_rows)
        self._sync_rows()

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        self.changed.emit()
        self._editor.retranslate()

    # -- loading -----------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        """Read what the page shows from the core: which profiles bring rules, the plan
        composed with the pool, and the rules of a profile that runs."""
        library = self._library

        async def read() -> tuple[Routing, ProfileList, ProfileRules | None, str]:
            routing = await library.inspect_all()
            error = ""
            try:
                routing = await library.compose_report()
            except (HostError, ProfileError) as failure:
                error = failure_text(failure)
            profiles = await library.current()
            rules = None
            if routing.mode == MODE_PROFILE and profiles.active is not None:
                rules = await library.profile_rules(profiles.active)
            return routing, profiles, rules, error

        def done(result: tuple[Routing, ProfileList, ProfileRules | None, str]) -> None:
            routing, profiles, rules, error = result
            self._list = profiles
            self._profile_rules = rules
            self._error = error
            self._loaded(routing)

        self._runner.submit(read(), done, self._failed)

    @Slot()
    def reload(self) -> None:
        """Take up what another page changed, without asking the core anything new."""

        async def read() -> tuple[Routing, ProfileList]:
            return await self._library.routing(), await self._library.current()

        def done(result: tuple[Routing, ProfileList]) -> None:
            self._list = result[1]
            self._loaded(result[0])

        self._runner.submit(read(), done, self._failed)

    def _loaded(self, routing: Routing) -> None:
        self._routing = routing
        self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self._busy = False
        self._error = failure_text(error)
        self.changed.emit()

    def _act(
        self,
        action: Coroutine[Any, Any, Routing],
        done: Callable[[], None] | None = None,
        failed: Callable[[str], None] | None = None,
    ) -> None:
        """Run a library action that changes the routing; its failure goes to ``failed``, or
        to the header."""
        self._error = ""
        self._busy = True
        self.changed.emit()

        def succeeded(routing: Routing) -> None:
            self._busy = False
            self._loaded(routing)
            self.routingChanged.emit()
            self.applied.emit()
            if done is not None:
                done()
            self.refresh()

        def refused(error: BaseException) -> None:
            self._busy = False
            if failed is not None:
                failed(failure_text(error))
                self.changed.emit()
            else:
                self._failed(error)

        self._runner.submit(action, succeeded, refused)

    # -- the mode ----------------------------------------------------------------------------
    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def modeOptions(self) -> list[dict[str, str]]:
        return [{"value": mode, "label": self.tr(label)} for mode, label in MODE_LABELS.items()]

    @Property(str, notify=changed)
    def mode(self) -> str:
        # Before the first import nothing was chosen; the plans side says what to do.
        return self._routing.mode or MODE_PLAN

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode in MODE_LABELS and mode != self._routing.mode:
            self._flush()
            self._clear_undo()
            self._act(self._library.set_mode(mode))

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def summary(self) -> str:
        if self._error:
            return self._error
        if self._routing.mode == MODE_PROFILE:
            active = self._list.active_profile()
            if active is None:
                return self.tr("订阅里没有规则")
            return self.tr("使用「{name}」自带的规则").format(name=active.name)
        plan = self._plan()
        if plan is None:
            return self.tr("尚未导入订阅")
        nodes = self._pool_size()
        if nodes is None:
            return self.tr("使用「{name}」").format(name=plan.name)
        if nodes == 0:
            return self.tr("使用「{name}」 · 节点池为空").format(name=plan.name)
        pool = self.tr("节点池 %n 个节点", "", nodes)
        return self.tr("使用「{name}」 · {pool}").format(name=plan.name, pool=pool)

    @Property(bool, notify=changed)
    def hasError(self) -> bool:
        return bool(self._error)

    @Property(str, notify=changed)
    def usingLabel(self) -> str:
        """What the overview's 当前使用 row says (routing.md §4.6)."""
        if self._routing.mode == MODE_PROFILE:
            active = self._list.active_profile()
            if active is not None:
                return self.tr("「{name}」自带的规则").format(name=active.name)
            return self.tr("未选择")
        plan = self._plan()
        return self.tr("「{name}」方案").format(name=plan.name) if plan else self.tr("未选择")

    @Property(str, notify=changed)
    def sidebarLabel(self) -> str:
        if self._routing.mode == MODE_PROFILE:
            active = self._list.active_profile()
            if active is not None:
                return self.tr("「{name}」的规则").format(name=active.name)
        elif self._routing.mode == MODE_PLAN and (plan := self._plan()) is not None:
            return self.tr("「{name}」方案").format(name=plan.name)
        return self.tr("未导入订阅")

    def _pool_size(self) -> int | None:
        if not any(p.in_pool for p in self._list.profiles):
            return 0
        report = self._routing.report
        return None if report is None else len(report.nodes)

    # -- undo --------------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def canUndo(self) -> bool:
        return self._undo is not None

    @Property(str, notify=changed)
    def undoText(self) -> str:
        return self._undo[0] if self._undo else ""

    @Property(str, notify=changed)
    def undoName(self) -> str:
        """What the 撤销 button does, for screen readers."""
        return self._undo[1] if self._undo else ""

    @Slot()
    def undo(self) -> None:
        if self._undo is None:
            return
        before = self._undo[2]
        self._clear_undo()
        self._commit(before)

    def _clear_undo(self) -> None:
        self._undo_timer.stop()
        self._undo = None

    def _expire_undo(self) -> None:
        self._undo = None
        self.changed.emit()

    # -- the profile's own rules -------------------------------------------------------------
    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def profileOptions(self) -> list[dict[str, object]]:
        options = []
        for profile in self._list.profiles:
            inspection = self._routing.inspections.get(profile.id)
            if inspection is None:
                note, enabled = "", True
            elif inspection.has_rules:
                note, enabled = self.tr("%n 条规则", "", inspection.rule_count), True
            else:
                note, enabled = self.tr("只有节点"), False
            options.append(
                {"value": profile.id, "label": profile.name, "note": note, "enabled": enabled}
            )
        return options

    @Property(str, notify=changed)
    def profileId(self) -> str:
        return self._list.active or ""

    @Property(bool, notify=changed)
    def hasRulesProfile(self) -> bool:
        """Some profile brings rules, or may: one not read yet counts."""
        return any(self._routing.has_rules(p.id) is not False for p in self._list.profiles)

    @Property(bool, notify=changed)
    def hasProfiles(self) -> bool:
        return bool(self._list.profiles)

    @Slot(str)
    def useProfile(self, profile_id: str) -> None:
        if profile_id != self._list.active or self._routing.mode != MODE_PROFILE:

            async def use() -> Routing:
                await self._library.activate(profile_id)
                return await self._library.routing()

            self._act(use())

    @Property(bool, notify=changed)
    def profileRunning(self) -> bool:
        return self._profile_rules is not None

    @Property(str, notify=changed)
    def profileCounts(self) -> str:
        """The size of the profile in use, when the core does not run it."""
        active = self._list.active
        inspection = self._routing.inspections.get(active) if active else None
        if inspection is None:
            return ""
        groups = self.tr("%n 个策略组", "", inspection.group_count)
        rules = self.tr("%n 条规则", "", inspection.rule_count)
        return self.tr("{groups}、{rules} · 连接后逐条列出").format(groups=groups, rules=rules)

    @Property(QObject, constant=True)
    def profileGroups(self) -> KeyedListModel:
        return self._source_groups

    @Property(QObject, constant=True)
    def profileRules(self) -> KeyedListModel:
        return self._source_rules

    @Property(str, notify=changed)
    def profileGroupCount(self) -> str:
        count = len(self._profile_rules.groups) if self._profile_rules else 0
        return self.tr("%n 个", "", count)

    @Property(str, notify=changed)
    def profileRuleCount(self) -> str:
        if self._profile_rules is None:
            return ""
        if self._rule_query:
            return self.tr("匹配 %n 条", "", len(self._source_rules.rows))
        return self.tr("%n 条", "", len(self._profile_rules.rules))

    @Property(bool, notify=changed)
    def profileRulesEmpty(self) -> bool:
        """The search matches none of the running profile's rules."""
        return self._profile_rules is not None and not self._source_rules.rows

    @Property(str, notify=changed)
    def ruleQuery(self) -> str:
        return self._rule_query

    @Slot(str)
    def setRuleQuery(self, query: str) -> None:
        if query != self._rule_query:
            self._rule_query = query
            self.changed.emit()

    # -- plans -------------------------------------------------------------------------------
    def _plan(self) -> Plan | None:
        """The plan in use as the page shows it: with the changes not stored yet."""
        stored = self._routing.plan()
        if self._draft is not None and stored is not None and self._draft.id == stored.id:
            return self._draft
        return stored

    def plan(self) -> Plan | None:
        return self._plan()

    def report(self) -> CompositionReport | None:
        return self._routing.report

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def planOptions(self) -> list[dict[str, str]]:
        return [{"value": p.id, "label": p.name} for p in self._routing.plans]

    @Property(str, notify=changed)
    def planId(self) -> str:
        plan = self._plan()
        return plan.id if plan else ""

    @Property(str, notify=changed)
    def planName(self) -> str:
        plan = self._plan()
        return plan.name if plan else ""

    @Property(bool, notify=changed)
    def canRemovePlan(self) -> bool:
        return len(self._routing.plans) > 1

    @Slot(str)
    def selectPlan(self, plan_id: str) -> None:
        if plan_id != self.planId:
            self._flush()
            self._clear_undo()
            self._act(self._library.select_plan(plan_id))

    @Property(str, notify=changed)
    def planError(self) -> str:
        return self._plan_error

    @Slot()
    def clearPlanError(self) -> None:
        self._plan_error = ""
        self.changed.emit()

    @Property(str, notify=changed)
    def newPlanName(self) -> str:
        return self.tr("新方案")

    @Property(str, notify=changed)
    def copyPlanName(self) -> str:
        return self.tr("{name} 副本").format(name=self.planName)

    @Slot(str, str)
    def createPlan(self, name: str, base: str) -> None:
        """A new plan from the template (``base`` empty) or a copy of the plan ``base``."""
        if not name.strip():
            return
        self._flush()
        self._clear_undo()
        self._plan_error = ""
        self._act(
            self._library.create_plan(name.strip(), base or None),
            self.planSaved.emit,
            self._plan_failed,
        )

    @Slot(str)
    def renamePlan(self, name: str) -> None:
        plan = self._plan()
        if plan is None or not name.strip():
            return
        self._plan_error = ""
        self._commit(
            replace(plan, name=name.strip()), done=self.planSaved.emit, failed=self._plan_failed
        )

    def _plan_failed(self, message: str) -> None:
        self._plan_error = message

    @Slot()
    def requestRemovePlan(self) -> None:
        plan = self._plan()
        if plan is not None and self.canRemovePlan:
            self._removing_plan = plan
            self.changed.emit()
            self.planRemovalRequested.emit()

    @Property(str, notify=changed)
    def planRemovalTitle(self) -> str:
        plan = self._removing_plan
        return self.tr("删除「{name}」？").format(name=plan.name) if plan else ""

    @Property(str, notify=changed)
    def planRemovalText(self) -> str:
        plan = self._removing_plan
        if plan is None:
            return ""
        rules = self.tr("%n 条规则", "", len(plan.rules))
        gone = self.tr("方案里的{rules}会一起删除，无法恢复。").format(rules=rules)
        successor = next((p for p in self._routing.plans if p.id != plan.id), None)
        if self._routing.mode == MODE_PLAN and successor is not None:
            text = self.tr("这是正在使用的方案，删除后改用「{successor}」。{deleted}")
            return text.format(successor=successor.name, deleted=gone)
        return gone

    @Slot()
    def confirmRemovePlan(self) -> None:
        plan, self._removing_plan = self._removing_plan, None
        if plan is not None:
            self._flush()
            self._clear_undo()
            self._act(self._library.delete_plan(plan.id))

    # -- rules -------------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def rules(self) -> KeyedListModel:
        """The plan's rule entries as RuleRow items."""
        return self._rules

    @Property(bool, notify=changed)
    def poolEmpty(self) -> bool:
        return self._pool_size() == 0

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def targetOptions(self) -> list[dict[str, object]]:
        return text.target_options(self._plan(), self._routing.report)

    @Property(str, notify=changed)
    def fallbackTarget(self) -> str:
        plan = self._plan()
        return plan.fallback if plan else plans.PROXY

    @Property(str, notify=changed)
    def fallbackLabel(self) -> str:
        plan = self._plan()
        return text.target_label(plan.fallback if plan else plans.PROXY, plan)

    @Property(str, notify=changed)
    def fallbackWarning(self) -> str:
        return self._warnings().get(plans.FALLBACK_ENTRY, "")

    def _warnings(self) -> dict[str, str]:
        report = self._routing.report
        if report is None:
            return {}
        return {w.entry_id: text.warning_text(w.reason) for w in report.warnings}

    def entry_names(self) -> dict[str, str]:
        """The names of the rule entries in use, for the connections page (§4.6)."""
        if self._routing.mode != MODE_PLAN:
            return {}
        plan = self._plan()
        names = {rule.id: text.rule_name(rule) for rule in plan.rules} if plan else {}
        names[plans.FALLBACK_ENTRY] = self.tr("其他流量")
        return names

    def pool_sources(self) -> tuple[dict[str, str], frozenset[str]]:
        """For the proxies page: each pool node's subscription, and the region groups' names;
        empty unless the tunnel routes with a plan."""
        report = self._routing.report
        if self._routing.mode != MODE_PLAN or report is None:
            return {}, frozenset()
        names = {p.id: p.name for p in self._list.profiles}
        sources = {node.name: names.get(node.source_id, "") for node in report.nodes}
        regions = frozenset(g.name for g in report.groups if g.kind == "region")
        return sources, regions

    @Slot(str, bool)
    def setRuleEnabled(self, rule_id: str, enabled: bool) -> None:
        self._patch_rules(
            lambda rules: [replace(r, enabled=enabled) if r.id == rule_id else r for r in rules]
        )

    @Slot(str)
    def removeRule(self, rule_id: str) -> None:
        plan = self._plan()
        rule = next((r for r in plan.rules if r.id == rule_id), None) if plan else None
        if plan is None or rule is None:
            return
        self._flush_timer_only()
        removed = replace(plan, rules=tuple(r for r in plan.rules if r.id != rule_id))
        name = text.rule_name(rule)
        status = self.tr("已删除「{name}」").format(name=name)
        action = self.tr("撤销删除「{name}」").format(name=name)
        self._commit(removed, undo=(status, action, plan))

    @Slot(str, int)
    def moveRule(self, rule_id: str, to: int) -> None:
        """Put the rule at index ``to`` of the list without it, as a drop does; the moves of
        about a second go as one change."""
        plan = self._plan()
        if plan is None:
            return
        rules = list(plan.rules)
        index = next((i for i, r in enumerate(rules) if r.id == rule_id), None)
        if index is None:
            return
        rule = rules.pop(index)
        to = max(0, min(to, len(rules)))
        if to == index:
            return
        rules.insert(to, rule)
        self._clear_undo()
        self._draft = replace(plan, rules=tuple(rules))
        self._error = ""
        self._merge.start()
        self.changed.emit()

    @Slot(str, int)
    def moveRuleBy(self, rule_id: str, delta: int) -> None:
        plan = self._plan()
        if plan is None:
            return
        index = next((i for i, r in enumerate(plan.rules) if r.id == rule_id), None)
        if index is not None:
            self.moveRule(rule_id, index + delta)

    @Slot(str)
    def setFallback(self, target: str) -> None:
        plan = self._plan()
        if plan is not None and target != plan.fallback:
            self._flush_timer_only()
            self._commit(replace(plan, fallback=target))

    def _patch_rules(self, change: Callable[[list[PlanRule]], list[PlanRule]]) -> None:
        plan = self._plan()
        if plan is not None:
            self._flush_timer_only()
            self._commit(replace(plan, rules=tuple(change(list(plan.rules)))))

    def add_rules(
        self,
        rules: list[PlanRule],
        replacing: str | None,
        done: Callable[[], None],
        failed: Callable[[str], None],
    ) -> None:
        """For the rule dialog: new entries go first, as exceptions come before the general
        rules (routing.md §4.4); an edited one keeps its place. The dialog stays open with
        the core's error when the change is refused."""
        plan = self._plan()
        if plan is None:
            return
        self._flush_timer_only()
        if replacing is None:
            changed = (*rules, *plan.rules)
        else:
            changed = tuple(rules[0] if r.id == replacing else r for r in plan.rules)
        self._commit(replace(plan, rules=changed), done=done, failed=failed, shown=False)

    # -- groups ------------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def builtinGroups(self) -> KeyedListModel:
        return self._builtin_groups

    @Property(QObject, constant=True)
    def regionGroups(self) -> KeyedListModel:
        return self._region_groups

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def groupTypeOptions(self) -> list[dict[str, str]]:
        return [{"value": t, "label": text.type_label(t)} for t in text.GROUP_TYPES]

    @Slot(str, str)
    def setGroupType(self, group_id: str, group_type: str) -> None:
        if group_type in plans.GROUP_TYPES:
            self._patch_group(group_id, lambda g: replace(g, type=group_type))

    @Slot(str, bool)
    def setGroupHidden(self, group_id: str, hidden: bool) -> None:
        self._patch_group(group_id, lambda g: replace(g, hidden=hidden))

    def _patch_group(self, group_id: str, change: Callable[[PlanGroup], PlanGroup]) -> None:
        plan = self._plan()
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
        self._flush_timer_only()
        self._commit(replace(plan, groups=tuple(groups)))

    # -- the node pool's settings ------------------------------------------------------------
    @Property(str, notify=changed)
    def infoPattern(self) -> str:
        return self._routing.info_pattern

    @Property(str, constant=True)
    def defaultInfoPattern(self) -> str:
        return DEFAULT_INFO_PATTERN

    @Property(str, notify=changed)
    def infoError(self) -> str:
        return self._info_error

    @Slot(str)
    def saveInfoPattern(self, pattern: str) -> None:
        self._info_error = ""
        stored = None if pattern == DEFAULT_INFO_PATTERN else pattern

        def failed(message: str) -> None:
            self._info_error = message

        self._act(self._library.set_info_pattern(stored), self.infoPatternSaved.emit, failed)

    @Slot()
    def clearInfoError(self) -> None:
        self._info_error = ""
        self.changed.emit()

    # -- the rule dialog ---------------------------------------------------------------------
    @Property(RuleEditor, constant=True)
    def editor(self) -> RuleEditor:
        return self._editor

    # -- applying ----------------------------------------------------------------------------
    def _commit(
        self,
        plan: Plan,
        *,
        undo: tuple[str, str, Plan] | None = None,
        done: Callable[[], None] | None = None,
        failed: Callable[[str], None] | None = None,
        shown: bool = True,
    ) -> None:
        """Store ``plan``: shown straight away unless ``shown`` is false, as for a dialog
        that waits for the core's answer. A refusal puts the stored plan back."""
        self._merge.stop()
        if shown:
            self._draft = plan
        self._clear_undo()
        if undo is not None:
            self._undo = undo
            self._undo_timer.start()
        self._error = ""
        self.changed.emit()

        def stored(routing: Routing) -> None:
            if self._draft is plan:
                self._draft = None
            self._loaded(routing)
            self.routingChanged.emit()
            self.applied.emit()
            if done is not None:
                done()

        def refused(error: BaseException) -> None:
            self._draft = None
            self._merge.stop()
            self._clear_undo()
            if failed is not None:
                failed(failure_text(error))
                self.changed.emit()
            else:
                self._failed(error)

        self._runner.submit(self._library.put_plan(plan), stored, refused)

    def _flush(self) -> None:
        """Store the moves still waiting, before another kind of change."""
        if self._merge.isActive():
            self._store_moves()

    def _store_moves(self) -> None:
        if self._draft is not None:
            self._commit(self._draft)

    def _flush_timer_only(self) -> None:
        # The next change carries the moves along: it starts from the plan as shown.
        self._merge.stop()

    # -- rows --------------------------------------------------------------------------------
    def _sync_rows(self) -> None:
        plan = self._plan()
        warnings = self._warnings()
        rows = []
        rules = plan.rules if plan else ()
        for n, rule in enumerate(rules):
            rows.append(
                RuleRow(
                    id=rule.id,
                    name=text.rule_name(rule),
                    caption=text.rule_caption(rule),
                    mono=rule.kind == "raw",
                    target=text.target_label(rule.target, plan),
                    targetId=rule.target,
                    active=rule.enabled,
                    warning=warnings.get(rule.id, "") if rule.enabled else "",
                    first=n == 0,
                    last=n == len(rules) - 1,
                    editable=rule.kind != "raw",
                )
            )
        self._rules.sync(rows)
        self._sync_groups(plan)
        self._sync_source()

    def _sync_groups(self, plan: Plan | None) -> None:
        report = self._routing.report
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
            builtin.append(self._group_row(group_id, group, group_type, members))
        self._builtin_groups.sync(builtin)
        region_rows = []
        for code, count in regions:
            group_id = f"region:{code}"
            group = settings.get(group_id)
            members = self.tr("%n 个节点", "", count)
            region_rows.append(
                self._group_row(group_id, group, group.type if group else "url-test", members)
            )
        self._region_groups.sync(region_rows)

    def _group_row(
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
        name = region_name(code) if code else text.target_label(group_id, None)
        return GroupRow(group_id, name, caption, group_type, hidden)

    def _sync_source(self) -> None:
        source = self._profile_rules
        if source is None:
            self._source_groups.sync([])
            self._source_rules.sync([])
            return
        self._source_groups.sync(
            [
                SourceGroupRow(
                    g.name,
                    g.name,
                    self.tr("{type} · %n 个成员", "", len(g.proxies)).format(type=g.type),
                )
                for g in source.groups
            ]
        )
        query = self._rule_query.strip().lower()
        rows = []
        for n, rule in enumerate(source.rules):
            match = rule.type if not rule.payload else f"{rule.type},{rule.payload}"
            if query and query not in f"{match} {rule.proxy}".lower():
                continue
            rows.append(SourceRuleRow(str(n), match, rule.proxy))
        self._source_rules.sync(rows)
