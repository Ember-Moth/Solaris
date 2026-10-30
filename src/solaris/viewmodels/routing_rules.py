"""The rules tab of 我的方案 (docs/routing.md §4.3): the plan's rule entries, then 其他流量,
with the rule test, the search and the target filter above them.

A change shows at once and goes to the library through RoutingViewModel, which keeps the
undo and stores the moves once they pause. The rule test asks the core which rule a domain
or an IP matches in the plan as shown (MatchRule, routing.md §8.3); the search and the
target filter only narrow the list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import RuleMatch
from solaris.models import plans
from solaris.models.library import ProfileLibrary
from solaris.models.routing import Plan, PlanRule
from solaris.viewmodels import routing_text as text
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel
from solaris.viewmodels.feedback import failure_text

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

TEST_MS = 400  # the rule test runs once typing pauses this long
ALL_TARGETS = ""  # the target filter's 全部目标


@dataclass(frozen=True)
class RuleRow:
    id: str
    name: str
    caption: str
    mono: bool
    target: str
    targetId: str
    active: bool  # enabled; ``enabled`` is an Item's own property in QML
    warning: str  # the target is unavailable at the moment; empty otherwise
    first: bool
    last: bool
    editable: bool
    hit: bool  # the rule test matched it

    @property
    def ruleId(self) -> str:  # QML reserves ``id``
        return self.id


def _fields(row_type: type, id_role: str) -> list[str]:
    """The roles of a row type: its fields, with ``id`` under another name for QML."""
    return [id_role if name == "id" else name for name in row_type.__dataclass_fields__]


@QmlElement
@QmlUncreatable("PlanRules is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class PlanRules(QObject):
    changed = Signal()
    testChanged = Signal()

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
        self._query = ""
        self._target_filter = ALL_TARGETS
        self._test_text = ""
        self._test: RuleMatch | None = None
        self._test_error = ""
        self._testing = False
        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.setInterval(TEST_MS)
        self._test_timer.timeout.connect(self._run_test)
        self._rows = KeyedListModel(_fields(RuleRow, "ruleId"), self)
        self.changed.connect(self._sync)
        self.testChanged.connect(self._sync)
        routing.changed.connect(self.changed)
        self._sync()

    def retranslate(self) -> None:
        self.changed.emit()
        self.testChanged.emit()

    # -- the rules ---------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def rows(self) -> KeyedListModel:
        """The plan's rule entries as RuleRow items."""
        return self._rows

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def targetOptions(self) -> list[dict[str, object]]:
        return text.target_options(self._routing.plan(), self._routing.report())

    @Property(str, notify=changed)
    def fallbackTarget(self) -> str:
        plan = self._routing.plan()
        return plan.fallback if plan else plans.PROXY

    @Property(str, notify=changed)
    def fallbackLabel(self) -> str:
        plan = self._routing.plan()
        return text.target_label(
            plan.fallback if plan else plans.PROXY,
            plan,
            self._routing.report(),
            self._routing.networks(),
        )

    @Property(str, notify=changed)
    def fallbackWarning(self) -> str:
        return self._warnings().get(plans.FALLBACK_ENTRY, "")

    def _warnings(self) -> dict[str, str]:
        """What the rule rows say of their targets' warnings, by entry id."""
        report = self._routing.report()
        if report is None:
            return {}
        return {
            w.entry_id: warning for w in report.warnings if (warning := text.warning_text(w.reason))
        }

    @Slot(str, bool)
    def setRuleEnabled(self, rule_id: str, enabled: bool) -> None:
        self._patch_rules(
            lambda rules: [replace(r, enabled=enabled) if r.id == rule_id else r for r in rules]
        )

    @Slot(str)
    def removeRule(self, rule_id: str) -> None:
        plan = self._routing.plan()
        rule = next((r for r in plan.rules if r.id == rule_id), None) if plan else None
        if plan is None or rule is None:
            return
        removed = replace(plan, rules=tuple(r for r in plan.rules if r.id != rule_id))
        name = text.rule_name(rule)
        status = self.tr("已删除「{name}」").format(name=name)
        action = self.tr("撤销删除「{name}」").format(name=name)
        self._routing.commit(removed, undo=(status, action, plan))

    @Slot(str, int)
    def moveRule(self, rule_id: str, to: int) -> None:
        """Put the rule at index ``to`` of the list without it, as a drop does; the moves of
        about a second go as one change."""
        plan = self._routing.plan()
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
        self._routing.show_moved(replace(plan, rules=tuple(rules)))

    @Slot(str, int)
    def moveRuleBy(self, rule_id: str, delta: int) -> None:
        plan = self._routing.plan()
        if plan is None:
            return
        index = next((i for i, r in enumerate(plan.rules) if r.id == rule_id), None)
        if index is not None:
            self.moveRule(rule_id, index + delta)

    @Slot(str)
    def setFallback(self, target: str) -> None:
        plan = self._routing.plan()
        if plan is not None and target != plan.fallback:
            self._routing.commit(replace(plan, fallback=target))

    def _patch_rules(self, change: Callable[[list[PlanRule]], list[PlanRule]]) -> None:
        plan = self._routing.plan()
        if plan is not None:
            self._routing.commit(replace(plan, rules=tuple(change(list(plan.rules)))))

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
        plan = self._routing.plan()
        if plan is None:
            return
        if replacing is None:
            changed = (*rules, *plan.rules)
        else:
            changed = tuple(rules[0] if r.id == replacing else r for r in plan.rules)
        self._routing.commit(replace(plan, rules=changed), done=done, failed=failed, shown=False)

    # -- the rule test (routing.md §4.3) -----------------------------------------------------
    @Property(str, notify=testChanged)
    def testText(self) -> str:
        return self._test_text

    @Slot(str)
    def setTestText(self, value: str) -> None:
        """Test what was typed once typing pauses; an empty box clears the result."""
        if value == self._test_text:
            return
        self._test_text = value
        if not value.strip():
            self._test_timer.stop()
            self._test, self._test_error = None, ""
        else:
            self._test_timer.start()
        self.testChanged.emit()

    @Slot()
    def runTest(self) -> None:
        """Test now, as Enter in the box does."""
        self._test_timer.stop()
        self._run_test()

    def _run_test(self) -> None:
        plan = self._routing.plan()
        written = self._test_text
        if plan is None or not written.strip() or not self._routing.core_available():
            return
        target = plans.match_target(written)
        if target is None:
            self._test, self._test_error = (
                None,
                self.tr("「{text}」不是域名或 IP").format(text=written.strip()),
            )
            self.testChanged.emit()
            return
        self._testing = True
        self.testChanged.emit()

        def matched(result: RuleMatch) -> None:
            self._testing = False
            if written == self._test_text:
                self._test, self._test_error = result, ""
            self.testChanged.emit()

        def failed(error: BaseException) -> None:
            self._testing = False
            if written == self._test_text:
                self._test, self._test_error = None, failure_text(error)
            self.testChanged.emit()

        self._runner.submit(self._library.plans.match_rule(plan, *target), matched, failed)

    @Property(str, notify=testChanged)
    def testResult(self) -> str:
        """命中「Telegram」 → 🇯🇵 日本 → 东京 03, as routing.md §4.3 writes it."""
        if self._test_error:
            return self._test_error
        result = self._test
        if result is None:
            return ""
        chain = " → ".join(text.outbound_label(name) for name in result.chain)
        if not result.matched or result.rule is None:
            line = self.tr("没有规则匹配 → {chain}").format(chain=chain)
        elif result.rule.entry_id:
            name = self._entry_name(result.rule.entry_id)
            line = self.tr("命中「{name}」 → {chain}").format(name=name, chain=chain)
        else:
            rule = text.rule_line(result.rule)
            line = self.tr("命中覆写的规则 {rule} → {chain}").format(rule=rule, chain=chain)
        if result.resolved_ip:
            line = self.tr("{line}（解析为 {ip}）").format(line=line, ip=result.resolved_ip)
        return line

    def _entry_name(self, entry_id: str) -> str:
        if entry_id == plans.FALLBACK_ENTRY:
            return self.tr("其他流量")
        plan = self._routing.plan()
        rule = next((r for r in plan.rules if r.id == entry_id), None) if plan else None
        return text.rule_name(rule) if rule else entry_id

    @Property(bool, notify=testChanged)
    def testFailed(self) -> bool:
        return bool(self._test_error)

    @Property(bool, notify=testChanged)
    def testing(self) -> bool:
        return self._testing

    def _test_hit(self) -> str:
        """The entry the test matched: a rule's id, the fallback's, or ""."""
        result = self._test
        if result is None or result.rule is None or self._test_error:
            return ""
        return result.rule.entry_id

    @Property(bool, notify=testChanged)
    def fallbackHit(self) -> bool:
        return self._test_hit() == plans.FALLBACK_ENTRY

    def retest(self) -> None:
        """The plan changed under the result: test the same text again."""
        if self._test_text.strip():
            self._test_timer.start()

    def core_changed(self, available: bool) -> None:
        """A core started or stopped answering; without one there is nothing to test with."""
        if not available:
            self._test_timer.stop()
            self._test, self._test_error = None, ""
        self.testChanged.emit()

    # -- the search and the target filter ----------------------------------------------------
    @Property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self.changed.emit()

    @Property(str, notify=changed)
    def targetFilter(self) -> str:
        return self._target_filter

    @Slot(str)
    def setTargetFilter(self, target: str) -> None:
        if target != self._target_filter:
            self._target_filter = target
            self.changed.emit()

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def targetFilterOptions(self) -> list[dict[str, object]]:
        """全部目标, then the targets the rules use, in the target dropdown's order."""
        plan = self._routing.plan()
        report = self._routing.report()
        used = {r.target for r in plan.rules} if plan else set()
        if self._target_filter:
            used.add(self._target_filter)
        options: list[dict[str, object]] = [{"value": ALL_TARGETS, "label": self.tr("全部目标")}]
        known = text.target_options(plan, report)
        listed = [o for o in known if o["value"] in used]
        listed += [
            {"value": t, "label": text.target_label(t, plan, report, self._routing.networks())}
            for t in sorted(used - {str(o["value"]) for o in known})
        ]
        for n, option in enumerate(listed):
            options.append(
                {"value": option["value"], "label": option["label"], "separator": n == 0}
            )
        return options

    @Property(str, notify=changed)
    def targetFilterLabel(self) -> str:
        if not self._target_filter:
            return self.tr("全部目标")
        return text.target_label(
            self._target_filter,
            self._routing.plan(),
            self._routing.report(),
            self._routing.networks(),
        )

    @Property(bool, notify=changed)
    def filtering(self) -> bool:
        """The list shows part of the rules, so dragging is off (DESIGN.md "可排序列表")."""
        return bool(self._query.strip() or self._target_filter)

    def _shown_count(self) -> int:
        plan = self._routing.plan()
        return sum(self._shown(rule, plan) for rule in plan.rules) if plan else 0

    @Property(bool, notify=changed)
    def filteredEmpty(self) -> bool:
        return self.filtering and self._shown_count() == 0

    def _shown(self, rule: PlanRule, plan: Plan) -> bool:
        if self._target_filter and rule.target != self._target_filter:
            return False
        query = self._query.strip().lower()
        if not query:
            return True
        haystack = " ".join(
            (
                text.rule_name(rule),
                text.rule_caption(rule),
                text.target_label(
                    rule.target, plan, self._routing.report(), self._routing.networks()
                ),
            )
        )
        return query in haystack.lower()

    # -- rows --------------------------------------------------------------------------------
    def _sync(self) -> None:
        plan = self._routing.plan()
        report = self._routing.report()
        warnings = self._warnings()
        hit = self._test_hit()
        rows = []
        rules = plan.rules if plan else ()
        for n, rule in enumerate(rules):
            if plan is None or not self._shown(rule, plan):
                continue
            rows.append(
                RuleRow(
                    id=rule.id,
                    name=text.rule_name(rule),
                    caption=text.rule_caption(rule),
                    mono=rule.kind == "raw",
                    target=text.target_label(rule.target, plan, report, self._routing.networks()),
                    targetId=rule.target,
                    active=rule.enabled,
                    warning=warnings.get(rule.id, "") if rule.enabled else "",
                    # Moves go by the whole list, filtered or not.
                    first=n == 0,
                    last=n == len(rules) - 1,
                    editable=rule.kind != "raw",
                    hit=rule.id == hit,
                )
            )
        self._rows.sync(rows)
