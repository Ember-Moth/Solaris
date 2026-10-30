"""How the routing page names targets, rule entries and groups (docs/routing.md §4.3)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mihomo_desktop.host.base import CompositionReport
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP, current_language
from mihomo_desktop.models import plans, services
from mihomo_desktop.models.regions import region_code, region_id, region_name
from mihomo_desktop.models.routing import DIRECT, REJECT, Plan, PlanGroup, PlanRule

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


def tr(text: str) -> str:
    """Translate text marked with QT_TRANSLATE_NOOP in this module's context."""
    return QCoreApplication.translate(CONTEXT, text)


def language() -> str:
    return current_language()


def type_label(group_type: str) -> str:
    return tr(GROUP_TYPES.get(group_type, group_type))


def target_label(target: str, plan: Plan | None) -> str:
    """A rule's target as the page names it: built-in groups and outbounds translated,
    regions by their localized names, custom groups by the plan's names."""
    if target in BUILTIN_NAMES:
        return tr(BUILTIN_NAMES[target])
    if target in OUTBOUND_NAMES:
        return tr(OUTBOUND_NAMES[target])
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


def target_options(plan: Plan | None, report: CompositionReport | None) -> list[dict[str, object]]:
    """The target dropdown's fixed sections (DESIGN.md "目标下拉框"): built-in groups, the
    regions with nodes, custom groups, then direct and reject; a separator opens each."""
    hidden = {g.id for g in (plan.groups if plan else ()) if g.hidden and g.kind == "region"}
    sections = [
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
        [(DIRECT, target_label(DIRECT, plan)), (REJECT, target_label(REJECT, plan))],
    ]
    options: list[dict[str, object]] = []
    for section in sections:
        for n, (value, label) in enumerate(section):
            options.append({"value": value, "label": label, "separator": n == 0 and bool(options)})
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


def warning_text(reason: str) -> str:
    if reason == "group_empty":
        return QCoreApplication.translate("RoutingViewModel", "没有成员，暂走节点选择")
    return QCoreApplication.translate("RoutingViewModel", "无节点，暂走节点选择")


def group_name(group: PlanGroup | None, group_id: str) -> str:
    if group is not None and group.kind == "custom":
        return group.name
    return target_label(group_id, None)
