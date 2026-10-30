"""Plans as the routing page edits them, and as the core reads them (docs/routing.md §6, §8.5).

A rule entry's ``content`` depends on its kind:

    service    {"service": "telegram"}                  expanded with the service list
    category   {"type": "GEOSITE", "value": "cn"}      GEOIP categories do not resolve names
    domain     {"items": ["example.com", "=www.example.org", "~tracker"]}
    ip         {"items": ["10.0.0.0/8", "2001:db8::/32"], "no_resolve": true}
    raw        {"items": [{"type": "PROCESS-NAME", "value": "ssh"}]}   M8b; passed through

A domain is a suffix match as written, ``=`` makes it a full match and ``~`` a keyword.
"""

from __future__ import annotations

import ipaddress
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import QCoreApplication

from mihomo_desktop.i18n import current_language
from mihomo_desktop.models import services
from mihomo_desktop.models.regions import region_id
from mihomo_desktop.models.routing import DIRECT, REJECT, Plan, PlanGroup, PlanRule

PLAN_VERSION = 1
PROXY = "proxy"  # 节点选择
AUTO = "auto"  # 自动选择
FALLBACK_ENTRY = "fallback"  # the entry id of the rule for other traffic
GROUP_TYPES = ("select", "url-test", "fallback", "load-balance")
FULL_PREFIX = "="
KEYWORD_PREFIX = "~"
# Labels of letters, digits, hyphens and underscores; any script, as IDNs are written.
_DOMAIN = re.compile(r"[\w-]+(\.[\w-]+)*", re.UNICODE)


def ui_locale() -> str:
    """The plan's locale (routing.md §8.5): the UI's language names the generated groups."""
    return current_language()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# -- the default template (routing.md §6) -----------------------------------------------------
TEMPLATE_RULES = (
    ("ads", services.REJECT),
    ("private", services.DIRECT),
    ("openai", region_id("US")),
    ("anthropic", region_id("US")),
    ("gemini", region_id("US")),
    ("cn", services.DIRECT),
)


def builtin_groups() -> tuple[PlanGroup, ...]:
    return (PlanGroup(PROXY, "builtin", "select"), PlanGroup(AUTO, "builtin", "url-test"))


def default_plan(plan_id: str | None = None, name: str | None = None) -> Plan:
    """The template: the built-in groups, a few services and other traffic to 节点选择. A
    target region without nodes goes to 节点选择 until it has some (routing.md §5.3)."""
    rules = tuple(
        PlanRule(new_id("r"), "service", {"service": service}, target)
        for service, target in TEMPLATE_RULES
    )
    if name is None:
        name = QCoreApplication.translate("Plans", "日常")
    return Plan(plan_id or new_id("p"), name, PROXY, builtin_groups(), rules)


def copy_plan(plan: Plan, name: str) -> Plan:
    """A new plan with the same groups and rules; rules get ids of their own."""
    rules = tuple(replace(rule, id=new_id("r")) for rule in plan.rules)
    return replace(plan, id=new_id("p"), name=name, rules=rules)


# -- rule entries ------------------------------------------------------------------------------
def domain_item(written: str) -> dict[str, Any]:
    if written.startswith(FULL_PREFIX):
        return {"type": "DOMAIN", "value": written[1:]}
    if written.startswith(KEYWORD_PREFIX):
        return {"type": "DOMAIN-KEYWORD", "value": written[1:]}
    return {"type": "DOMAIN-SUFFIX", "value": written}


def ip_item(cidr: str, no_resolve: bool) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "IP-CIDR6" if ":" in cidr else "IP-CIDR", "value": cidr}
    if no_resolve:
        item["no_resolve"] = True
    return item


def rule_items(rule: PlanRule) -> list[dict[str, Any]]:
    """The mihomo rules an entry stands for, without the target; empty when it stands for
    none any more, such as a service the list no longer has."""
    content = rule.content if isinstance(rule.content, dict) else {}
    match rule.kind:
        case "service":
            found = services.service(str(content.get("service", "")))
            return [match.item() for match in found.matches] if found else []
        case "category":
            item: dict[str, Any] = {"type": content["type"], "value": content["value"]}
            if content["type"] == services.GEOIP:
                item["no_resolve"] = True
            return [item]
        case "domain":
            return [domain_item(str(written)) for written in content.get("items", ())]
        case "ip":
            no_resolve = bool(content.get("no_resolve", True))
            return [ip_item(str(cidr), no_resolve) for cidr in content.get("items", ())]
        case _:
            return [dict(item) for item in content.get("items", ())]


def plan_json(plan: Plan, locale: str) -> str:
    """The plan as the core reads it (routing.md §8.5). Entries that stand for no rule are
    left out, so the core never sees an empty one."""
    groups: list[dict[str, Any]] = []
    for group in plan.groups:
        entry: dict[str, Any] = {"id": group.id, "kind": group.kind, "type": group.type}
        if group.name and group.kind == "custom":
            entry["name"] = group.name
        if group.hidden:
            entry["hidden"] = True
        if group.conditions and group.kind != "region":
            entry["members"] = group.conditions
        if group.options:
            entry["options"] = group.options
        groups.append(entry)
    rules: list[dict[str, Any]] = []
    for rule in plan.rules:
        items = rule_items(rule)
        if not items:
            continue
        entry = {"id": rule.id, "kind": rule.kind, "enabled": rule.enabled, "items": items}
        entry["target"] = rule.target
        if rule.kind == "service":
            entry["service"] = rule.content.get("service")  # the UI's own field
        rules.append(entry)
    document = {
        "version": PLAN_VERSION,
        "locale": locale,
        "groups": groups,
        "rules": rules,
        "fallback": plan.fallback,
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def services_in(plan: Plan) -> set[str]:
    return {
        str(rule.content.get("service"))
        for rule in plan.rules
        if rule.kind == "service" and isinstance(rule.content, dict)
    }


def categories_in(plan: Plan) -> set[tuple[str, str]]:
    return {
        (str(rule.content.get("type")), str(rule.content.get("value")))
        for rule in plan.rules
        if rule.kind == "category" and isinstance(rule.content, dict)
    }


# -- what the add-rule dialog reads from its text boxes ---------------------------------------
@dataclass(frozen=True)
class Parsed:
    items: tuple[str, ...]  # as the plan stores them
    invalid: tuple[tuple[int, str], ...]  # 1-based line numbers and their text
    suffixes: int = 0
    full: int = 0
    keywords: int = 0


def _lines(text: str) -> Iterable[tuple[int, str]]:
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            yield number, line.strip()


def parse_domains(text: str) -> Parsed:
    """One domain a line: as written for a suffix match, after ``=`` for a full match and
    after ``~`` for a keyword. Blank lines are skipped; a value with blanks or commas, or a
    domain that is not one, makes its line invalid."""
    items: list[str] = []
    invalid: list[tuple[int, str]] = []
    counts = {"suffix": 0, "full": 0, "keyword": 0}
    for number, line in _lines(text):
        if line.startswith(KEYWORD_PREFIX):
            value, kind = line[1:].strip(), "keyword"
            valid = bool(value) and not re.search(r"[\s,]", value)
        else:
            full = line.startswith(FULL_PREFIX)
            value, kind = (line[1:].strip(), "full") if full else (line, "suffix")
            valid = _DOMAIN.fullmatch(value) is not None
        if not valid:
            invalid.append((number, line))
            continue
        written = {"suffix": value, "full": FULL_PREFIX + value}.get(kind, KEYWORD_PREFIX + value)
        if written not in items:
            items.append(written)
            counts[kind] += 1
    return Parsed(tuple(items), tuple(invalid), counts["suffix"], counts["full"], counts["keyword"])


def parse_cidrs(text: str) -> Parsed:
    """One IPv4 or IPv6 range a line; an address alone is a range of one."""
    items: list[str] = []
    invalid: list[tuple[int, str]] = []
    for number, line in _lines(text):
        try:
            cidr = str(ipaddress.ip_network(line, strict=False))
        except ValueError:
            invalid.append((number, line))
            continue
        if cidr not in items:
            items.append(cidr)
    return Parsed(tuple(items), tuple(invalid))


# Targets a rule may name besides the plan's groups.
OUTBOUNDS = (DIRECT, REJECT)
