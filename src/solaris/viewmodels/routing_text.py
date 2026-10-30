"""How the routing page names targets, rule entries and groups (docs/routing.md §4.3)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from solaris.host.base import CompositionReport, Rule
from solaris.i18n import QT_TRANSLATE_NOOP, current_language
from solaris.models import plans, services
from solaris.models.regions import region_code, region_id, region_name
from solaris.models.routing import DIRECT, REJECT, Plan, PlanGroup, PlanRule

CONTEXT = "RoutingViewModel"

GROUP_TYPES = {
    "url-test": QT_TRANSLATE_NOOP("RoutingViewModel", "自动选择"),
    "select": QT_TRANSLATE_NOOP("RoutingViewModel", "手动选择"),
    "fallback": QT_TRANSLATE_NOOP("RoutingViewModel", "故障转移"),
    "load-balance": QT_TRANSLATE_NOOP("RoutingViewModel", "负载均衡"),
}
BUILTIN_NAMES = {
    plans.PROXY: QT_TRANSLATE_NOOP("RoutingViewModel", "节点选择"),
    plans.AUTO: QT_TRANSLATE_NOOP("RoutingViewModel", "自动选择"),
}
OUTBOUND_NAMES = {
    DIRECT: QT_TRANSLATE_NOOP("RoutingViewModel", "直连"),
    REJECT: QT_TRANSLATE_NOOP("RoutingViewModel", "拒绝"),
}
EXIT_NAMES = {plans.WARP: QT_TRANSLATE_NOOP("RoutingViewModel", "WARP 出口")}
WARNINGS = {
    "region_empty": QT_TRANSLATE_NOOP("RoutingViewModel", "无节点，暂走节点选择"),
    "group_empty": QT_TRANSLATE_NOOP("RoutingViewModel", "没有成员，暂走节点选择"),
    # The rules that name an exit or a mesh outbound reject instead (D21, docs/mesh.md §3).
    "egress_unavailable": QT_TRANSLATE_NOOP("RoutingViewModel", "不可用，已拒绝连接"),
    "mesh_missing": QT_TRANSLATE_NOOP("RoutingViewModel", "不存在，已拒绝连接"),
}


def tr(text: str) -> str:
    """Translate text marked with QT_TRANSLATE_NOOP in this module's context."""
    return QCoreApplication.translate(CONTEXT, text)


def language() -> str:
    return current_language()


def type_label(group_type: str) -> str:
    return tr(GROUP_TYPES.get(group_type, group_type))


def target_label(target: str, plan: Plan | None, report: CompositionReport | None = None) -> str:
    """A rule's target as the page names it: built-in groups, outbounds and exits translated,
    regions by their localized names, custom groups by the plan's names, mesh outbounds by
    their names in the configuration (the report's) or else in their profile."""
    if target in BUILTIN_NAMES:
        return tr(BUILTIN_NAMES[target])
    if target in OUTBOUND_NAMES:
        return tr(OUTBOUND_NAMES[target])
    if target in EXIT_NAMES:
        return tr(EXIT_NAMES[target])
    if plans.is_mesh(target):
        found = next((m for m in report.mesh if m.id == target), None) if report else None
        return found.name if found else plans.mesh_name(target)
    code = region_code(target)
    if code is not None:
        return region_name(code)
    group = next((g for g in plan.groups if g.id == target), None) if plan else None
    return group.name if group and group.name else target


def regions_with_nodes(report: CompositionReport | None) -> list[tuple[str, int]]:
    """The region codes the pool has nodes in, in table order, with their node counts."""
    if report is None:
        return []
    return [(g.region, g.node_count) for g in report.groups if g.kind == "region"]


def _group_sections(
    plan: Plan | None, report: CompositionReport | None
) -> list[list[tuple[str, str]]]:
    hidden = {g.id for g in (plan.groups if plan else ()) if g.hidden and g.kind == "region"}
    return [
        [
            (plans.PROXY, target_label(plans.PROXY, plan)),
            (plans.AUTO, target_label(plans.AUTO, plan)),
        ],
        [
            (region_id(code), region_name(code))
            for code, _ in regions_with_nodes(report)
            if region_id(code) not in hidden
        ],
        [(g.id, g.name) for g in (plan.groups if plan else ()) if g.kind == "custom"],
    ]


def _sections(sections: list[list[tuple[str, str]]]) -> list[dict[str, object]]:
    """Options with a separator before each section but the first; empty ones drop out."""
    options: list[dict[str, object]] = []
    for section in sections:
        for n, (value, label) in enumerate(section):
            options.append({"value": value, "label": label, "separator": n == 0 and bool(options)})
    return options


def group_options(plan: Plan | None, report: CompositionReport | None) -> list[dict[str, object]]:
    """The groups the WARP exit may go out through: built-in groups, the regions with nodes,
    then custom groups."""
    return _sections(_group_sections(plan, report))


def target_options(plan: Plan | None, report: CompositionReport | None) -> list[dict[str, object]]:
    """The target dropdown's fixed sections (DESIGN.md "目标下拉框", docs/mesh.md §5): the
    groups, the mesh outbounds, the chained exits, then direct and reject. An exit the plan
    has not turned on cannot be picked and says so."""
    options = _sections(
        [
            *_group_sections(plan, report),
            [(m.id, m.name) for m in (report.mesh if report else ())],
            [(plans.WARP, target_label(plans.WARP, plan))],
            [(DIRECT, target_label(DIRECT, plan)), (REJECT, target_label(REJECT, plan))],
        ]
    )
    if not plans.warp_exit(plan).enabled:
        off = QCoreApplication.translate("RoutingViewModel", "未启用")
        warp = next(o for o in options if o["value"] == plans.WARP)
        warp.update(note=off, enabled=False)
    return options


def service_name(service_id: str) -> str:
    found = services.service(service_id)
    return found.name(language()) if found else service_id


def rule_name(rule: PlanRule) -> str:
    content = rule.content if isinstance(rule.content, dict) else {}
    match rule.kind:
        case "service":
            return service_name(str(content.get("service", "")))
        case "category":
            return f"{content.get('type', '')} · {content.get('value', '')}"
        case "domain":
            return QCoreApplication.translate(
                "RoutingViewModel", "自定义域名（%n 个）", None, len(content.get("items", ()))
            )
        case "ip":
            return QCoreApplication.translate(
                "RoutingViewModel", "IP 段（%n 个）", None, len(content.get("items", ()))
            )
        case _:
            items = content.get("items", ())
            return " · ".join(f"{i.get('type')},{i.get('value')}" for i in items)


def rule_caption(rule: PlanRule) -> str:
    """What the entry matches, then whether it is off."""
    content = rule.content if isinstance(rule.content, dict) else {}
    separator = QCoreApplication.translate("RoutingViewModel", "、")
    match rule.kind:
        case "service":
            found = services.service(str(content.get("service", "")))
            if found is None:
                text = QCoreApplication.translate("RoutingViewModel", "服务清单里已没有这个服务")
            else:
                by_type: dict[str, list[str]] = {}
                for match in found.matches:
                    by_type.setdefault(match.type, []).append(match.value)
                text = " · ".join(f"{t} {separator.join(v)}" for t, v in by_type.items())
        case "category":
            text = QCoreApplication.translate("RoutingViewModel", "分类")
        case "domain":
            text = separator.join(str(item) for item in content.get("items", ()))
        case "ip":
            text = separator.join(str(item) for item in content.get("items", ()))
            if content.get("no_resolve", True):
                text += " · no-resolve"
        case _:
            text = QCoreApplication.translate("RoutingViewModel", "原始规则 · 只读")
    if not rule.enabled:
        text = QCoreApplication.translate("RoutingViewModel", "{caption} · 已停用").format(
            caption=text
        )
    return text


def outbound_label(name: str) -> str:
    """A name in the core's chain: mihomo's outbounds translated, groups and nodes as named."""
    if name == "REJECT-DROP":
        name = REJECT
    return tr(OUTBOUND_NAMES[name]) if name in OUTBOUND_NAMES else name


def rule_line(rule: Rule) -> str:
    """A rule as the configuration writes it, without its target: GEOSITE,telegram."""
    return rule.type if not rule.payload else f"{rule.type},{rule.payload}"


def warning_text(reason: str) -> str:
    """What a rule row says of its target's warning; empty for the reasons that concern no
    rule, such as an exit's via group without members."""
    return tr(WARNINGS[reason]) if reason in WARNINGS else ""


def group_name(group: PlanGroup | None, group_id: str) -> str:
    if group is not None and group.kind == "custom":
        return group.name
    return target_label(group_id, None)
