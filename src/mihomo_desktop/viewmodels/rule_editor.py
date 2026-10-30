"""The add-rule dialog (docs/routing.md §4.4): services, GEO categories, domains or IP ranges,
and where they go. Picking several services or categories adds one entry each; the domains
or ranges of one paste make one entry. New entries go to the top of the list.

The dialog stays open until the core accepts the change; a refusal shows under the form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QCoreApplication, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.host.base import GeoCategories, bundled_geo_categories
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP
from mihomo_desktop.models import plans, services
from mihomo_desktop.models.library import ProfileLibrary
from mihomo_desktop.models.regions import region_code, region_name
from mihomo_desktop.models.routing import PlanRule
from mihomo_desktop.viewmodels import routing_text as text
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.common.keyed_list import KeyedListModel

if TYPE_CHECKING:
    from mihomo_desktop.viewmodels.routing import RoutingViewModel

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

KINDS = {
    "service": QT_TRANSLATE_NOOP("RuleEditor", "服务"),
    "category": QT_TRANSLATE_NOOP("RuleEditor", "分类"),
    "domain": QT_TRANSLATE_NOOP("RuleEditor", "域名"),
    "ip": QT_TRANSLATE_NOOP("RuleEditor", "IP 段"),
}
GEO_KINDS = (services.GEOSITE, services.GEOIP)


@dataclass(frozen=True)
class ServiceRow:
    id: str
    name: str
    category: str  # the section it is listed under
    note: str
    picked: bool
    added: bool  # in the plan already; cannot be picked again

    @property
    def itemId(self) -> str:  # QML reserves ``id``
        return self.id


@dataclass(frozen=True)
class CategoryRow:
    id: str
    name: str
    note: str
    picked: bool
    added: bool

    @property
    def itemId(self) -> str:
        return self.id


def _roles(row_type: type) -> list[str]:
    return ["itemId" if name == "id" else name for name in row_type.__dataclass_fields__]


@QmlElement
@QmlUncreatable("RuleEditor is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class RuleEditor(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed
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
        self._geo: GeoCategories = bundled_geo_categories()
        self._services = KeyedListModel(_roles(ServiceRow), self)
        self._categories = KeyedListModel(_roles(CategoryRow), self)
        self._reset(None)
        self.changed.connect(self._sync_rows)

    def _reset(self, rule: PlanRule | None) -> None:
        self._rule = rule  # the entry being edited; None adds new ones
        self._kind = rule.kind if rule and rule.kind in KINDS else "service"
        content = rule.content if rule and isinstance(rule.content, dict) else {}
        self._service_query = ""
        self._picked: list[str] = (
            [str(content["service"])] if self._kind == "service" and rule else []
        )
        self._geo_kind = (
            str(content.get("type", services.GEOSITE))
            if self._kind == "category"
            else services.GEOSITE
        )
        self._category_query = ""
        self._categories_picked: list[str] = (
            [str(content["value"])] if self._kind == "category" and rule else []
        )
        items = [str(item) for item in content.get("items", ())]
        self._domain_text = "\n".join(items) if self._kind == "domain" else ""
        self._ip_text = "\n".join(items) if self._kind == "ip" else ""
        self._no_resolve = bool(content.get("no_resolve", True)) if self._kind == "ip" else True
        self._target = rule.target if rule else plans.PROXY
        self._target_touched = rule is not None
        self._saving = False
        self._error = ""

    # -- opening -----------------------------------------------------------------------------
    @Slot()
    def openAdd(self) -> None:
        self._open(None)

    @Slot(str)
    def openEdit(self, rule_id: str) -> None:
        plan = self._routing.plan()
        rule = next((r for r in plan.rules if r.id == rule_id), None) if plan else None
        if rule is not None and rule.kind != "raw":
            self._open(rule)

    def _open(self, rule: PlanRule | None) -> None:
        self._reset(rule)
        self.changed.emit()
        self.opened.emit()

        async def categories() -> GeoCategories:
            return await self._library.geo_categories()

        self._runner.submit(categories(), self._categories_read, lambda _: None)

    def _categories_read(self, categories: GeoCategories) -> None:
        self._geo = categories
        self.changed.emit()

    # -- what it adds ------------------------------------------------------------------------
    @Property(str, notify=changed)
    def title(self) -> str:
        return self.tr("编辑规则") if self._rule else self.tr("添加规则")

    @Property(str, notify=changed)
    def confirmLabel(self) -> str:
        if self._saving:
            return self.tr("正在保存…")
        return self.tr("保存") if self._rule else self.tr("添加")

    @Property(bool, notify=changed)
    def editing(self) -> bool:
        return self._rule is not None

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def kindOptions(self) -> list[dict[str, str]]:
        return [{"value": kind, "label": self.tr(label)} for kind, label in KINDS.items()]

    @Property(str, notify=changed)
    def kind(self) -> str:
        return self._kind

    @Slot(str)
    def setKind(self, kind: str) -> None:
        if kind in KINDS and kind != self._kind:
            self._kind = kind
            self._error = ""
            self.changed.emit()

    # -- services ----------------------------------------------------------------------------
    @Property(QObject, constant=True)
    def services(self) -> KeyedListModel:
        return self._services

    @Property(str, notify=changed)
    def serviceQuery(self) -> str:
        return self._service_query

    @Slot(str)
    def setServiceQuery(self, query: str) -> None:
        if query != self._service_query:
            self._service_query = query
            self.changed.emit()

    @Slot(str)
    def toggleService(self, service_id: str) -> None:
        if services.service(service_id) is None or service_id in self._added_services():
            return
        if self._rule is not None:
            self._picked = [service_id]
        elif service_id in self._picked:
            self._picked.remove(service_id)
        else:
            self._picked.append(service_id)
        if not self._target_touched and self._picked:
            first = services.service(self._picked[0])
            if first is not None:
                self._target = first.target
        self.changed.emit()

    def _added_services(self) -> set[str]:
        plan = self._routing.plan()
        added = plans.services_in(plan) if plan else set()
        if self._rule is not None and self._rule.kind == "service":
            added.discard(str(self._rule.content.get("service")))
        return added

    @Property(str, notify=changed)
    def serviceSummary(self) -> str:
        if not self._picked:
            return (
                self.tr("选择一个服务") if self._rule else self.tr("可以多选；每个服务生成一条规则")
            )
        names = self.tr("、").join(text.service_name(s) for s in self._picked)
        picked = self.tr("已选 %n 个：{names}", "", len(self._picked)).format(names=names)
        if self._rule:
            return picked
        adds = self.tr("将添加 %n 条规则", "", len(self._picked))
        return self.tr("{picked} · {adds}").format(picked=picked, adds=adds)

    @Property(bool, notify=changed)
    def servicesEmpty(self) -> bool:
        return not self._services.rows

    # -- GEO categories ----------------------------------------------------------------------
    @Property(QObject, constant=True)
    def categories(self) -> KeyedListModel:
        return self._categories

    @Property("QVariantList", constant=True)  # pyright: ignore[reportArgumentType]
    def geoOptions(self) -> list[dict[str, str]]:
        return [{"value": kind, "label": kind} for kind in GEO_KINDS]

    @Property(str, notify=changed)
    def geo(self) -> str:
        return self._geo_kind

    @Slot(str)
    def setGeo(self, geo: str) -> None:
        if geo in GEO_KINDS and geo != self._geo_kind:
            self._geo_kind = geo
            self._categories_picked = []
            self._category_query = ""
            self.changed.emit()

    @Property(str, notify=changed)
    def categoryQuery(self) -> str:
        return self._category_query

    @Slot(str)
    def setCategoryQuery(self, query: str) -> None:
        if query != self._category_query:
            self._category_query = query
            self.changed.emit()

    @Slot(str)
    def toggleCategory(self, name: str) -> None:
        if name in self._added_categories():
            return
        if self._rule is not None:
            self._categories_picked = [name]
        elif name in self._categories_picked:
            self._categories_picked.remove(name)
        else:
            self._categories_picked.append(name)
        self.changed.emit()

    def _added_categories(self) -> set[str]:
        plan = self._routing.plan()
        added = {v for t, v in plans.categories_in(plan) if t == self._geo_kind} if plan else set()
        if self._rule is not None and self._rule.kind == "category":
            added.discard(str(self._rule.content.get("value")))
        return added

    @Property(str, notify=changed)
    def categorySummary(self) -> str:
        if self._categories_picked:
            names = self.tr("、").join(self._categories_picked)
            return self.tr("已选 %n 个：{names}", "", len(self._categories_picked)).format(
                names=names
            )
        dataset = self._geo.geosite if self._geo_kind == services.GEOSITE else self._geo.geoip
        if dataset.bundled:
            return self.tr("内核还没有 GEO 数据，列出的是安装包附带的分类")
        return self.tr("分类列表来自内核正在使用的 GEO 数据")

    @Property(bool, notify=changed)
    def categoriesEmpty(self) -> bool:
        return not self._categories.rows

    # -- domains and IP ranges ---------------------------------------------------------------
    @Property(str, notify=changed)
    def domainText(self) -> str:
        return self._domain_text

    @Property(str, notify=changed)
    def ipText(self) -> str:
        return self._ip_text

    @Slot(str)
    def setText(self, value: str) -> None:
        """The text box of the kind shown."""
        if self._kind == "ip":
            if value == self._ip_text:
                return
            self._ip_text = value
        else:
            if value == self._domain_text:
                return
            self._domain_text = value
        self.changed.emit()

    def _parsed(self) -> plans.Parsed:
        if self._kind == "ip":
            return plans.parse_cidrs(self._ip_text)
        return plans.parse_domains(self._domain_text)

    @Property(str, notify=changed)
    def textSummary(self) -> str:
        parsed = self._parsed()
        if not parsed.items and not parsed.invalid:
            if self._kind == "ip":
                return self.tr("每行一个 CIDR，IPv4 和 IPv6 都可以")
            return self.tr("每行一个。直接写域名是后缀匹配，= 开头是完整匹配，~ 开头是关键字")
        found = self.tr("%n 个", "", len(parsed.items))
        if parsed.invalid:
            invalid = self.tr("%n 行无效", "", len(parsed.invalid))
            return self.tr("识别出 {found}，其中 {invalid}").format(found=found, invalid=invalid)
        return self.tr("识别出 {found}").format(found=found)

    @Property(str, notify=changed)
    def textError(self) -> str:
        parsed = self._parsed()
        if not parsed.invalid:
            return ""
        line, written = parsed.invalid[0]
        return self.tr("第 {line} 行无效：{text}").format(line=line, text=written)

    @Property(bool, notify=changed)
    def noResolve(self) -> bool:
        return self._no_resolve

    @Slot(bool)
    def setNoResolve(self, value: bool) -> None:
        if value != self._no_resolve:
            self._no_resolve = value
            self.changed.emit()

    # -- the target --------------------------------------------------------------------------
    @Property(str, notify=changed)
    def target(self) -> str:
        return self._target

    @Property(str, notify=changed)
    def targetLabel(self) -> str:
        return text.target_label(self._target, self._routing.plan())

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def targetOptions(self) -> list[dict[str, object]]:
        return text.target_options(self._routing.plan(), self._routing.report())

    @Slot(str)
    def setTarget(self, target: str) -> None:
        self._target = target
        self._target_touched = True
        self.changed.emit()

    @Property(str, notify=changed)
    def targetHint(self) -> str:
        code = region_code(self._target)
        regions = {c for c, _ in text.regions_with_nodes(self._routing.report())}
        if code is not None and self._routing.report() is not None and code not in regions:
            return self.tr("{region}暂时没有节点，暂走节点选择").format(region=region_name(code))
        if self._kind == "service" and self._picked and not self._target_touched:
            return self.tr("服务清单的建议")
        return ""

    # -- saving ------------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def canSave(self) -> bool:
        if self._saving:
            return False
        if self._kind == "service":
            return bool(self._picked)
        if self._kind == "category":
            return bool(self._categories_picked)
        parsed = self._parsed()
        return bool(parsed.items) and not parsed.invalid

    @Property(bool, notify=changed)
    def saving(self) -> bool:
        return self._saving

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    def _entries(self) -> list[PlanRule]:
        target = self._target
        match self._kind:
            case "service":
                contents = [{"service": s} for s in self._picked]
            case "category":
                contents = [{"type": self._geo_kind, "value": c} for c in self._categories_picked]
            case "domain":
                contents = [{"items": list(self._parsed().items)}]
            case _:
                contents = [{"items": list(self._parsed().items), "no_resolve": self._no_resolve}]
        if self._rule is not None:
            content = contents[0]
            return [PlanRule(self._rule.id, self._kind, content, target, self._rule.enabled)]
        return [PlanRule(plans.new_id("r"), self._kind, c, target) for c in contents]

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

        replacing = self._rule.id if self._rule else None
        self._routing.add_rules(self._entries(), replacing, done, failed)

    # -- rows --------------------------------------------------------------------------------
    def _sync_rows(self) -> None:
        language = text.language()
        query = self._service_query.strip().lower()
        added = self._added_services()
        categories = {
            c.id: QCoreApplication.translate("Services", c.name) for c in services.CATEGORIES
        }
        rows = []
        for service in services.SERVICES:
            section = categories[service.category]
            haystack = f"{service.name_zh} {service.name_en} {service.id} {section}".lower()
            if query and query not in haystack:
                continue
            is_added = service.id in added
            rows.append(
                ServiceRow(
                    service.id,
                    service.name(language),
                    section,
                    self.tr("已添加") if is_added else "",
                    is_added or service.id in self._picked,
                    is_added,
                )
            )
        self._services.sync(rows)
        dataset = self._geo.geosite if self._geo_kind == services.GEOSITE else self._geo.geoip
        query = self._category_query.strip().lower()
        added = self._added_categories()
        self._categories.sync(
            [
                CategoryRow(
                    name,
                    name,
                    self.tr("已添加") if name in added else "",
                    name in added or name in self._categories_picked,
                    name in added,
                )
                for name in dataset.names
                if not query or query in name
            ]
        )

    def retranslate(self) -> None:
        self.optionsChanged.emit()
        self.changed.emit()
