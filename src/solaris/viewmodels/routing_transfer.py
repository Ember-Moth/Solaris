"""Plans in and out (docs/routing.md §4.7): "以此创建我的方案" from the profile whose rules are
in use and "导入…" from a mihomo configuration go through one conversion in the core, whose
summary shows before anything is created; "导出…" and "查看生成的配置…" show YAML the core
writes, to copy or save.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.host.base import EXPORT_GENERATED, EXPORT_SHARE, PlanConversion
from solaris.models import plans
from solaris.models.library import ProfileLibrary
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

# Why a rule stays out of the plan (SkippedRule.reason), in the order the summary names them.
SKIP_REASONS = ("rule_set", "sub_rule", "target", "after_match", "invalid")


@dataclass(frozen=True)
class Pending:
    """A conversion that waits for 创建."""

    conversion: PlanConversion
    source: str  # what it came from: a profile's name, or a file's
    name: str  # the plan's suggested name


@QmlElement
@QmlUncreatable("PlanTransfer is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class PlanTransfer(QObject):
    changed = Signal()
    conversionReady = Signal()  # the view opens the summary
    converted = Signal()  # the plan is created; the view closes the summary
    yamlReady = Signal()  # the view opens the YAML

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
        self._pending: Pending | None = None
        self._busy = False
        self._error = ""  # why the last conversion or export did not happen
        self._saving = False
        self._yaml = ""
        self._yaml_kind = EXPORT_SHARE
        self._yaml_plan = ""
        self._notice = ""

    def retranslate(self) -> None:
        self.changed.emit()

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    # -- converting --------------------------------------------------------------------------
    @Slot()
    def convertProfile(self) -> None:
        """Convert the groups and rules of the profile in use (订阅自带)."""
        profile_id = self._routing.active_profile_id()
        name = self._routing.active_profile_name()
        if profile_id:
            self._convert(self._library.plans.convert_profile(profile_id), name)

    @Slot(str)
    def importFile(self, file_url: str) -> None:
        """Convert the mihomo configuration a FileDialog chose; it passes a file:// URL."""
        path = Path(QUrl(file_url).toLocalFile() or file_url)
        self._convert(self._library.plans.convert_file(path), path.stem)

    def _convert(self, conversion: Coroutine[Any, Any, PlanConversion], source: str) -> None:
        self._busy = True
        self._error = ""
        self.changed.emit()

        def ready(result: PlanConversion) -> None:
            self._busy = False
            self._pending = Pending(result, source, source)
            self.changed.emit()
            self.conversionReady.emit()

        def failed(error: BaseException) -> None:
            self._busy = False
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(conversion, ready, failed)

    @Property(str, notify=changed)
    def conversionTitle(self) -> str:
        pending = self._pending
        return self.tr("以「{name}」创建方案").format(name=pending.source) if pending else ""

    @Property(str, notify=changed)
    def conversionSummary(self) -> str:
        """What the plan will hold, as routing.md §4.7 words it, and what stays out."""
        pending = self._pending
        if pending is None:
            return ""
        c = pending.conversion
        groups = self.tr("%n 个策略组", "", c.group_count)
        if c.merged_group_count:
            merged = self.tr("（其中 %n 个并入内置组）", "", c.merged_group_count)
            groups = f"{groups}{merged}"
        rules = self.tr("%n 条规则", "", c.rule_count)
        if c.raw_rule_count:
            raw = self.tr("其中 %n 条保留为原始规则", "", c.raw_rule_count)
            summary = self.tr("{groups}、{rules}，{raw}。").format(
                groups=groups, rules=rules, raw=raw
            )
        else:
            summary = self.tr("{groups}、{rules}。").format(groups=groups, rules=rules)
        if c.entry_count:
            summary += self.tr(
                "方案里有 %n 条规则条目（相邻的同类规则合为一条）。", "", c.entry_count
            )
        if c.skipped:
            counts = Counter(rule.reason for rule in c.skipped)
            reasons = self.tr("、").join(
                self._reason(reason, counts[reason]) for reason in SKIP_REASONS if counts[reason]
            )
            skipped = self.tr("%n 条无法转换，不会带入方案", "", len(c.skipped))
            summary += self.tr("{skipped}：{reasons}。").format(skipped=skipped, reasons=reasons)
        return summary

    def _reason(self, reason: str, count: int) -> str:
        match reason:
            case "rule_set":
                return self.tr("规则集 %n 条", "", count)
            case "sub_rule":
                return self.tr("子规则 %n 条", "", count)
            case "target":
                return self.tr("目标不存在 %n 条", "", count)
            case "after_match":
                return self.tr("在 MATCH 之后 %n 条", "", count)
            case _:
                return self.tr("无法识别 %n 条", "", count)

    @Property("QStringList", notify=changed)  # pyright: ignore[reportArgumentType]
    def skippedRules(self) -> list[str]:
        pending = self._pending
        return [rule.rule for rule in pending.conversion.skipped] if pending else []

    @Property(str, notify=changed)
    def suggestedName(self) -> str:
        return self._pending.name if self._pending else ""

    @Property(bool, notify=changed)
    def saving(self) -> bool:
        return self._saving

    @Slot(str, bool)
    def create(self, name: str, use: bool) -> None:
        """Create the plan of the pending conversion; ``use`` routes with it from now on."""
        pending = self._pending
        if pending is None or not name.strip() or self._saving:
            return
        plan = plans.plan_from_json(pending.conversion.plan_json, plans.new_id("p"), name.strip())
        self._saving = True
        self._error = ""
        self.changed.emit()

        def done() -> None:
            self._saving = False
            self._pending = None
            self.changed.emit()
            self.converted.emit()

        def failed(message: str) -> None:
            self._saving = False
            self._error = message
            self.changed.emit()

        self._routing.adopt_plan(plan, use, done, failed)

    @Slot()
    def dismiss(self) -> None:
        """The summary closed without creating; the next conversion replaces it."""
        self._error = ""
        self.changed.emit()

    # -- YAML out ----------------------------------------------------------------------------
    @Slot()
    def exportPlan(self) -> None:
        self._show_yaml(EXPORT_SHARE)

    @Slot()
    def showGenerated(self) -> None:
        self._show_yaml(EXPORT_GENERATED)

    def _show_yaml(self, kind: str) -> None:
        plan = self._routing.plan()
        if plan is None:
            return
        self._busy = True
        self._error = ""
        self.changed.emit()

        def ready(yaml: str) -> None:
            self._busy = False
            self._yaml, self._yaml_kind, self._yaml_plan = yaml, kind, plan.name
            self._notice = ""
            self.changed.emit()
            self.yamlReady.emit()

        def failed(error: BaseException) -> None:
            self._busy = False
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.plans.export_plan(plan, kind), ready, failed)

    @Property(str, notify=changed)
    def yaml(self) -> str:
        return self._yaml

    @Property(bool, notify=changed)
    def sharing(self) -> bool:
        """The YAML is the export to share, not the generated configuration."""
        return self._yaml_kind == EXPORT_SHARE

    @Property(str, notify=changed)
    def yamlTitle(self) -> str:
        if self._yaml_kind == EXPORT_SHARE:
            return self.tr("导出「{name}」").format(name=self._yaml_plan)
        return self.tr("生成的配置")

    @Property(str, notify=changed)
    def yamlNote(self) -> str:
        if self._yaml_kind == EXPORT_SHARE:
            return self.tr("mihomo 格式的策略组和规则，不含节点。")
        return self.tr("交给内核的完整配置，节点的认证信息已隐去。")

    @Property(str, notify=changed)
    def exportName(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d")
        return f"{self._yaml_plan or 'plan'}-{stamp}.yaml"

    @Property(str, notify=changed)
    def notice(self) -> str:
        """What 复制 or 另存为… did, for the line under the YAML."""
        return self._notice

    @Slot()
    def copyYaml(self) -> None:
        QGuiApplication.clipboard().setText(self._yaml)
        self._notice = self.tr("已复制")
        self.changed.emit()

    @Slot(str)
    def saveYaml(self, file_url: str) -> None:
        """Write the YAML to the file a FileDialog chose; it passes a file:// URL."""
        path = Path(QUrl(file_url).toLocalFile() or file_url)
        try:
            path.write_text(self._yaml, encoding="utf-8", newline="\n")
        except OSError as error:
            detail = error.strerror or str(error)
            self._notice = self.tr("保存失败：{detail}").format(detail=detail)
        else:
            self._notice = self.tr("已保存到 {path}").format(path=path.name)
        self.changed.emit()
