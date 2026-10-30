"""订阅自带 on the routing page (docs/routing.md §4.3): which profile's own rules route, and
those rules. While the profile runs, its groups and rules are listed read only, as the core
runs them; otherwise the page says how many the core read from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris.models.library import ProfileLibrary, ProfileRules, Routing
from solaris.models.routing import MODE_PROFILE
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.common.keyed_list import KeyedListModel

if TYPE_CHECKING:
    from solaris.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1


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


def _fields(row_type: type) -> list[str]:
    return list(row_type.__dataclass_fields__)


@QmlElement
@QmlUncreatable("ProfileRoutes is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ProfileRoutes(QObject):
    changed = Signal()

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
        self._rules: ProfileRules | None = None  # while the profile runs
        self._query = ""
        self._groups = KeyedListModel(_fields(SourceGroupRow), self)
        self._rule_rows = KeyedListModel(_fields(SourceRuleRow), self)
        self.changed.connect(self._sync)
        routing.changed.connect(self.changed)
        self._sync()

    def retranslate(self) -> None:
        self.changed.emit()

    def set_rules(self, rules: ProfileRules | None) -> None:
        """What the page's refresh read of the profile in use; the routing's next change
        shows it."""
        self._rules = rules

    # -- the profile -------------------------------------------------------------------------
    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def options(self) -> list[dict[str, object]]:
        routing = self._routing.snapshot()
        options = []
        for profile in self._routing.profile_list().profiles:
            inspection = routing.inspections.get(profile.id)
            has_rules = routing.has_rules(profile)
            if has_rules is False:
                note, enabled = self.tr("只有节点"), False
            elif inspection is not None and has_rules:
                note, enabled = self.tr("%n 条规则", "", inspection.rule_count), True
            else:
                note, enabled = "", True
            options.append(
                {"value": profile.id, "label": profile.name, "note": note, "enabled": enabled}
            )
        return options

    @Property(str, notify=changed)
    def profileId(self) -> str:
        return self._routing.active_profile_id()

    @Property(bool, notify=changed)
    def hasRules(self) -> bool:
        """Some profile brings rules, or may: one not read yet counts."""
        routing = self._routing.snapshot()
        return any(routing.has_rules(p) is not False for p in self._routing.profile_list().profiles)

    @Property(bool, notify=changed)
    def hasProfiles(self) -> bool:
        return bool(self._routing.profile_list().profiles)

    @Slot(str)
    def use(self, profile_id: str) -> None:
        current = self._routing.profile_list().active
        if profile_id != current or self._routing.snapshot().mode != MODE_PROFILE:

            async def activate() -> Routing:
                await self._library.profiles.activate(profile_id)
                return await self._library.routing()

            self._routing.act(activate())

    # -- its groups and rules ----------------------------------------------------------------
    @Property(bool, notify=changed)
    def running(self) -> bool:
        return self._rules is not None

    @Property(str, notify=changed)
    def counts(self) -> str:
        """The size of the profile in use, when the core does not run it."""
        active = self._routing.profile_list().active
        inspection = self._routing.snapshot().inspections.get(active) if active else None
        if inspection is None:
            return ""
        groups = self.tr("%n 个策略组", "", inspection.group_count)
        rules = self.tr("%n 条规则", "", inspection.rule_count)
        return self.tr("{groups}、{rules} · 连接后逐条列出").format(groups=groups, rules=rules)

    @Property(QObject, constant=True)
    def groups(self) -> KeyedListModel:
        return self._groups

    @Property(QObject, constant=True)
    def rules(self) -> KeyedListModel:
        return self._rule_rows

    @Property(str, notify=changed)
    def groupCount(self) -> str:
        count = len(self._rules.groups) if self._rules else 0
        return self.tr("%n 个", "", count)

    @Property(str, notify=changed)
    def ruleCount(self) -> str:
        if self._rules is None:
            return ""
        if self._query:
            return self.tr("匹配 %n 条", "", len(self._rule_rows.rows))
        return self.tr("%n 条", "", len(self._rules.rules))

    @Property(bool, notify=changed)
    def rulesEmpty(self) -> bool:
        """The search matches none of the running profile's rules."""
        return self._rules is not None and not self._rule_rows.rows

    @Property(str, notify=changed)
    def query(self) -> str:
        return self._query

    @Slot(str)
    def setQuery(self, query: str) -> None:
        if query != self._query:
            self._query = query
            self.changed.emit()

    # -- rows --------------------------------------------------------------------------------
    def _sync(self) -> None:
        source = self._rules
        if source is None:
            self._groups.sync([])
            self._rule_rows.sync([])
            return
        self._groups.sync(
            [
                SourceGroupRow(
                    g.name,
                    g.name,
                    self.tr("{type} · %n 个成员", "", len(g.proxies)).format(type=g.type),
                )
                for g in source.groups
            ]
        )
        query = self._query.strip().lower()
        rows = []
        for n, rule in enumerate(source.rules):
            match = rule.type if not rule.payload else f"{rule.type},{rule.payload}"
            if query and query not in f"{match} {rule.proxy}".lower():
                continue
            rows.append(SourceRuleRow(str(n), match, rule.proxy))
        self._rule_rows.sync(rows)
