"""Routing ViewModel: the routing page (docs/routing.md §4.3). The routing mode and the user's
plans; the page's sections are parts of their own, which read the plan as shown and change
it through this one.

Every confirmed change goes to the library at once, which validates it before storing it and
reloads a running tunnel (§5.4). The page shows the change straight away; a rejected one
puts the stored plan back and says why in the header. Moves, by dragging or from the menu,
wait about a second for the next one and go as one change. A removed rule can be brought
back for eight seconds, or until the next change.

The parts: 订阅自带 (routing_profile.py); the plan's rules with the rule test
(routing_rules.py); its groups (routing_groups.py), the mesh networks (routing_mesh.py)
and the chained exits (routing_exits.py) on the groups tab; and the dialogs for rules
(rule_editor.py), groups (routing_groups.py) and plans in and out (routing_transfer.py).

What only the core can work out (the rule test, the group preview, conversion and export,
the pool's counts) is left out while no core answers: on macOS, while the tunnel is down
(``coreAvailable``, D27).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import CompositionReport, HostError, MeshNetwork, WarpAccount
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models import plans
from solaris.models.library import ProfileLibrary, ProfileRules, Routing
from solaris.models.profiles import ProfileError, ProfileList
from solaris.models.routing import DEFAULT_INFO_PATTERN, MODE_PLAN, MODE_PROFILE, Plan
from solaris.models.settings import SettingsStore
from solaris.models.vpngate import ServerList
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text
from solaris.viewmodels.routing_exits import VpnGateExit, WarpExit
from solaris.viewmodels.routing_groups import GroupEditor, PlanGroups
from solaris.viewmodels.routing_mesh import MeshNetworks
from solaris.viewmodels.routing_profile import ProfileRoutes
from solaris.viewmodels.routing_rules import PlanRules
from solaris.viewmodels.routing_transfer import PlanTransfer
from solaris.viewmodels.rule_editor import RuleEditor

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

MODE_LABELS = {
    MODE_PROFILE: QT_TRANSLATE_NOOP("RoutingViewModel", "订阅自带"),
    MODE_PLAN: QT_TRANSLATE_NOOP("RoutingViewModel", "我的方案"),
}
MERGE_MS = 1000  # moves that follow each other within this go as one change
UNDO_MS = 8000


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
        settings: SettingsStore,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._runner = runner
        self._core = library.core_available
        self._running = False  # as the overview last saw the tunnel
        self._routing: Routing = library.routing_snapshot()
        self._list: ProfileList = library.snapshot()
        self._draft: Plan | None = None  # shown, not stored yet
        self._error = ""
        self._busy = False
        self._undo: tuple[str, str, Plan] | None = None  # status text, button name, plan
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
        self._editor = RuleEditor(self, runner, library, self)
        self._group_editor = GroupEditor(self, runner, library, self)
        self._transfer = PlanTransfer(self, runner, library, self)
        self._warp = WarpExit(self, runner, library, settings, self)
        self._vpngate = VpnGateExit(self, runner, library, self)
        self._mesh_networks = MeshNetworks(self, runner, library, self)
        self._profile_routes = ProfileRoutes(self, runner, library, self)
        self._plan_rules = PlanRules(self, runner, library, self)
        self._plan_groups = PlanGroups(self, runner, library, self)

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        self.changed.emit()
        self._editor.retranslate()
        self._group_editor.retranslate()
        self._transfer.retranslate()
        self._warp.retranslate()
        self._vpngate.retranslate()
        self._mesh_networks.retranslate()
        self._profile_routes.retranslate()
        self._plan_rules.retranslate()
        self._plan_groups.retranslate()

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
                routing = await library.plans.compose_report()
            except (HostError, ProfileError) as failure:
                error = failure_text(failure)
            profiles = await library.current()
            rules = None
            if routing.mode == MODE_PROFILE and profiles.active is not None:
                rules = await library.plans.profile_rules(profiles.active)
            return routing, profiles, rules, error

        def done(result: tuple[Routing, ProfileList, ProfileRules | None, str]) -> None:
            routing, profiles, rules, error = result
            self._list = profiles
            self._profile_routes.set_rules(rules)
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

    def act(
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
            self._plan_rules.retest()
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
            self.act(self._library.plans.set_mode(mode))

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
        plan = self.plan()
        if plan is None:
            return self.tr("尚未导入订阅")
        nodes = self.pool_size()
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
        plan = self.plan()
        return self.tr("「{name}」方案").format(name=plan.name) if plan else self.tr("未选择")

    @Property(str, notify=changed)
    def sidebarLabel(self) -> str:
        if self._routing.mode == MODE_PROFILE:
            active = self._list.active_profile()
            if active is not None:
                return self.tr("「{name}」的规则").format(name=active.name)
        elif self._routing.mode == MODE_PLAN and (plan := self.plan()) is not None:
            return self.tr("「{name}」方案").format(name=plan.name)
        return self.tr("未导入订阅")

    @Property(bool, notify=changed)
    def poolEmpty(self) -> bool:
        """Shown under the plan on both tabs."""
        return self.pool_size() == 0

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
        self.commit(before)

    def _clear_undo(self) -> None:
        self._undo_timer.stop()
        self._undo = None

    def _expire_undo(self) -> None:
        self._undo = None
        self.changed.emit()

    # -- what the parts read -----------------------------------------------------------------
    def plan(self) -> Plan | None:
        """The plan in use as the page shows it: with the changes not stored yet."""
        stored = self._routing.plan()
        if self._draft is not None and stored is not None and self._draft.id == stored.id:
            return self._draft
        return stored

    def snapshot(self) -> Routing:
        """The routing as the library last reported it."""
        return self._routing

    def profile_list(self) -> ProfileList:
        return self._list

    def report(self) -> CompositionReport | None:
        return self._routing.report

    def pool_size(self) -> int | None:
        """The nodes in the pool: 0 when no profile joins it, None until the core composed it."""
        if not any(p.in_pool for p in self._list.profiles):
            return 0
        report = self._routing.report
        return None if report is None else len(report.nodes)

    def warp_account(self) -> WarpAccount | None:
        return self._routing.warp

    def vpngate_list(self) -> ServerList:
        return self._routing.vpngate

    def networks(self) -> tuple[MeshNetwork, ...]:
        """The mesh networks the user added, without their secrets."""
        return self._routing.networks

    def network_secrets(self) -> dict[str, frozenset[str]]:
        """The names of each network's secrets the credential store holds, by network id."""
        return dict(self._routing.secrets)

    def routes_with_plan(self) -> bool:
        return self._routing.mode == MODE_PLAN

    def tunnel_running(self, running: bool) -> None:
        """The overview saw the tunnel start or stop; the exits may have waited for it,
        and the mesh networks have a status from now on.
        On macOS a core answers only while the tunnel runs, and a connection first reads
        what was stored while none could (D27). The tunnel runs once that is stored: then the
        page reads what it shows again, and the subscriptions page what the connection read."""
        self._warp.set_running(running)
        self._vpngate.set_running(running)
        self._mesh_networks.set_running(running)
        core = self._library.core_available
        if (running, core) == (self._running, self._core):
            return
        self._running, self._core = running, core
        self.changed.emit()
        self._plan_rules.core_changed(core)
        self._group_editor.core_changed()
        self.routingChanged.emit()
        self.refresh()

    def core_available(self) -> bool:
        return self._core

    @Property(bool, notify=changed)
    def coreAvailable(self) -> bool:
        return self._core

    def adopt(self, routing: Routing) -> None:
        """Take up what a library action outside the plan changed, such as the WARP account:
        the report, and the configuration a running tunnel reloaded."""
        self._loaded(routing)
        self.routingChanged.emit()
        self.applied.emit()
        self._plan_rules.retest()

    def profile_names(self) -> dict[str, str]:
        """The profiles' names by id, for the subscriptions a pool node came from."""
        return {p.id: p.name for p in self._list.profiles}

    def active_profile_id(self) -> str:
        return self._list.active or ""

    def active_profile_name(self) -> str:
        active = self._list.active_profile()
        return active.name if active else ""

    def rule_editor(self) -> RuleEditor:
        return self._editor

    def plan_rules(self) -> PlanRules:
        return self._plan_rules

    def plan_groups(self) -> PlanGroups:
        return self._plan_groups

    # -- what the other pages read -----------------------------------------------------------
    def entry_names(self) -> dict[str, str]:
        """The names of the rule entries in use, for the connections page (§4.6)."""
        if self._routing.mode != MODE_PLAN:
            return {}
        plan = self.plan()
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

    # -- plans -------------------------------------------------------------------------------
    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def planOptions(self) -> list[dict[str, str]]:
        return [{"value": p.id, "label": p.name} for p in self._routing.plans]

    @Property(str, notify=changed)
    def planId(self) -> str:
        plan = self.plan()
        return plan.id if plan else ""

    @Property(str, notify=changed)
    def planName(self) -> str:
        plan = self.plan()
        return plan.name if plan else ""

    @Property(bool, notify=changed)
    def canRemovePlan(self) -> bool:
        return len(self._routing.plans) > 1

    @Slot(str)
    def selectPlan(self, plan_id: str) -> None:
        if plan_id != self.planId:
            self._flush()
            self._clear_undo()
            self.act(self._library.plans.select_plan(plan_id))

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
        self.act(
            self._library.plans.create_plan(name.strip(), base or None),
            self.planSaved.emit,
            self._plan_failed,
        )

    @Slot(str)
    def renamePlan(self, name: str) -> None:
        plan = self.plan()
        if plan is None or not name.strip():
            return
        self._plan_error = ""
        self.commit(
            replace(plan, name=name.strip()), done=self.planSaved.emit, failed=self._plan_failed
        )

    def _plan_failed(self, message: str) -> None:
        self._plan_error = message

    @Slot()
    def requestRemovePlan(self) -> None:
        plan = self.plan()
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
            self.act(self._library.plans.delete_plan(plan.id))

    def adopt_plan(
        self, plan: Plan, use: bool, done: Callable[[], None], failed: Callable[[str], None]
    ) -> None:
        """For a converted plan: store it, and route with it when ``use``."""
        self._flush()
        self._clear_undo()
        self.act(self._library.plans.add_plan(plan, use), done, failed)

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

        self.act(self._library.plans.set_info_pattern(stored), self.infoPatternSaved.emit, failed)

    @Slot()
    def clearInfoError(self) -> None:
        self._info_error = ""
        self.changed.emit()

    # -- the parts, for QML ------------------------------------------------------------------
    @Property(ProfileRoutes, constant=True)
    def profileRoutes(self) -> ProfileRoutes:
        return self._profile_routes

    @Property(PlanRules, constant=True)
    def planRules(self) -> PlanRules:
        return self._plan_rules

    @Property(RuleEditor, constant=True)
    def editor(self) -> RuleEditor:
        return self._editor

    @Property(PlanGroups, constant=True)
    def planGroups(self) -> PlanGroups:
        return self._plan_groups

    @Property(GroupEditor, constant=True)
    def groupEditor(self) -> GroupEditor:
        return self._group_editor

    @Property(MeshNetworks, constant=True)
    def meshNetworks(self) -> MeshNetworks:
        return self._mesh_networks

    @Property(WarpExit, constant=True)
    def warp(self) -> WarpExit:
        return self._warp

    @Property(VpnGateExit, constant=True)
    def vpngate(self) -> VpnGateExit:
        return self._vpngate

    @Property(PlanTransfer, constant=True)
    def transfer(self) -> PlanTransfer:
        return self._transfer

    # -- applying ----------------------------------------------------------------------------
    def commit(
        self,
        plan: Plan,
        *,
        undo: tuple[str, str, Plan] | None = None,
        done: Callable[[], None] | None = None,
        failed: Callable[[str], None] | None = None,
        shown: bool = True,
    ) -> None:
        """Store ``plan``: shown straight away unless ``shown`` is false, as for a dialog
        that waits for the core's answer. A refusal puts the stored plan back. ``plan`` starts
        from the plan as shown, so the moves still waiting go with it; ``undo`` is the status
        text, the 撤销 button's name and the plan to go back to."""
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
            self._plan_rules.retest()
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

        self._runner.submit(self._library.plans.put_plan(plan), stored, refused)

    def show_moved(self, plan: Plan) -> None:
        """Show ``plan``, whose rules were moved; it is stored once the moves pause, or with
        the next change."""
        self._clear_undo()
        self._draft = plan
        self._error = ""
        self._merge.start()
        self.changed.emit()

    def _flush(self) -> None:
        """Store the moves still waiting, before another kind of change."""
        if self._merge.isActive():
            self._store_moves()

    def _store_moves(self) -> None:
        if self._draft is not None:
            self.commit(self._draft)
