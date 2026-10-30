"""The routing data of docs/routing.md §9: the routing mode and the user's plans, each with
its policy groups and rule entries. Only the data for now; the routing page comes with C1.

A plan is written whole, as the routing page saves it once the host has validated the
change (routing.md §5.4): groups and rules missing from it are marked deleted, so putting
the earlier plan back undoes a deletion. The profile in use, ``RoutingState.profile_id``,
is written by ProfileStore as ``ProfileList.active``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from mihomo_desktop.models.database import Database, sync_rows

MODE_PROFILE = "profile"  # 订阅自带: the rules of the profile in use
MODE_PLAN = "plan"  # 我的方案: the node pool with one of the user's plans
MODES = (MODE_PROFILE, MODE_PLAN)
GROUP_KINDS = ("builtin", "region", "custom")
RULE_KINDS = ("service", "category", "domain", "ip", "raw")
DIRECT = "DIRECT"
REJECT = "REJECT"
# Nodes whose names match carry information, such as the quota, not a server (§5.2).
DEFAULT_INFO_PATTERN = "(?i)剩余|到期|过期|流量|官网|套餐|expire|traffic|website"


@dataclass(frozen=True)
class RoutingState:
    mode: str | None = None  # None until one was chosen (routing.md §5.1)
    profile_id: str | None = None  # the profile MODE_PROFILE uses
    plan_id: str | None = None  # the plan MODE_PLAN uses
    info_pattern: str | None = None  # None for DEFAULT_INFO_PATTERN


@dataclass(frozen=True)
class PlanGroup:
    id: str  # unique in the plan; built-in and region groups have fixed ones
    kind: str  # one of GROUP_KINDS
    type: str  # the mihomo group type: select, url-test, fallback, load-balance
    name: str = ""  # custom groups; the app names built-in and region groups
    conditions: Any = field(default_factory=dict)  # which nodes it holds; JSON
    options: Any = field(default_factory=dict)  # advanced parameters; JSON
    hidden: bool = False


@dataclass(frozen=True)
class PlanRule:
    id: str
    kind: str  # one of RULE_KINDS
    content: Any  # what it matches; JSON
    target: str  # a group id of the plan, DIRECT or REJECT
    enabled: bool = True


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    fallback: str  # where other traffic goes, as a rule's target
    groups: tuple[PlanGroup, ...] = ()
    rules: tuple[PlanRule, ...] = ()


class RoutingStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def state(self) -> RoutingState:
        with self._database.reading() as connection:
            row = connection.execute(
                "SELECT mode, profile_id, plan_id, info_pattern FROM routing_state"
            ).fetchone()
        return RoutingState(*row)

    def update_state(self, **changes: Any) -> RoutingState:
        """Store the fields given; the others keep what is stored, whoever wrote it."""
        after = replace(RoutingState(), **changes)  # raises for a name that is no field
        if after.mode not in (None, *MODES):
            raise ValueError(f"invalid mode: {after.mode!r}")
        if not changes:
            return self.state()
        assigned = ", ".join(f"{name} = ?" for name in changes)
        with self._database.transaction() as connection:
            connection.execute(
                f"UPDATE routing_state SET {assigned}, updated_at = ?",
                (*changes.values(), self._database.clock()),
            )
        return self.state()

    def plans(self) -> tuple[Plan, ...]:
        """The plans in their order, without the deleted ones."""
        with self._database.reading() as connection:
            rows = connection.execute(
                "SELECT id, name, fallback FROM plans WHERE deleted_at IS NULL "
                "ORDER BY position, created_at"
            ).fetchall()
            return tuple(_plan(connection, row) for row in rows)

    def plan(self, plan_id: str) -> Plan:
        """The plan, unless it was deleted; raises KeyError otherwise."""
        with self._database.reading() as connection:
            row = connection.execute(
                "SELECT id, name, fallback FROM plans WHERE id = ? AND deleted_at IS NULL",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            return _plan(connection, row)

    def put_plan(self, plan: Plan) -> None:
        """Store the plan with its groups and rules, in their order. A new plan comes
        last; a deleted one is restored in its place."""
        for group in plan.groups:
            if group.kind not in GROUP_KINDS:
                raise ValueError(f"invalid group kind: {group.kind!r}")
        for rule in plan.rules:
            if rule.kind not in RULE_KINDS:
                raise ValueError(f"invalid rule kind: {rule.kind!r}")
        groups = [
            (g.id, g.kind, g.type, g.name, _json(g.conditions), _json(g.options), int(g.hidden), i)
            for i, g in enumerate(plan.groups)
        ]
        rules = [
            (r.id, i, int(r.enabled), r.kind, _json(r.content), r.target)
            for i, r in enumerate(plan.rules)
        ]
        with self._database.transaction() as connection:
            now = self._database.clock()
            row = connection.execute(
                "SELECT name, fallback, deleted_at FROM plans WHERE id = ?", (plan.id,)
            ).fetchone()
            if row is None:
                (position,) = connection.execute(
                    "SELECT COALESCE(MAX(position) + 1, 0) FROM plans"
                ).fetchone()
                connection.execute(
                    "INSERT INTO plans (id, name, fallback, position, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (plan.id, plan.name, plan.fallback, position, now, now),
                )
            elif tuple(row) != (plan.name, plan.fallback, None):
                connection.execute(
                    "UPDATE plans SET name = ?, fallback = ?, updated_at = ?, deleted_at = NULL "
                    "WHERE id = ?",
                    (plan.name, plan.fallback, now, plan.id),
                )
            parent = ("plan_id", plan.id)
            group_columns = ("id", "kind", "type", "name", "conditions", "options", "hidden")
            sync_rows(
                connection, "plan_groups", (*group_columns, "position"), groups, now, parent=parent
            )
            rule_columns = ("id", "position", "enabled", "kind", "content", "target")
            sync_rows(connection, "plan_rules", rule_columns, rules, now, parent=parent)

    def delete_plan(self, plan_id: str) -> None:
        """Mark the plan deleted; its groups and rules stay with it."""
        with self._database.transaction() as connection:
            now = self._database.clock()
            connection.execute(
                "UPDATE plans SET deleted_at = ?, updated_at = ? "
                "WHERE id = ? AND deleted_at IS NULL",
                (now, now, plan_id),
            )

    def order_plans(self, plan_ids: Sequence[str]) -> None:
        """Put the plans in this order; plans not named follow in the order they had."""
        with self._database.transaction() as connection:
            now = self._database.clock()
            stored = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM plans WHERE deleted_at IS NULL ORDER BY position, created_at"
                )
            ]
            first = [plan_id for plan_id in plan_ids if plan_id in stored]
            order = first + [plan_id for plan_id in stored if plan_id not in first]
            for position, plan_id in enumerate(order):
                connection.execute(
                    "UPDATE plans SET position = ?, updated_at = ? "
                    "WHERE id = ? AND position IS NOT ?",
                    (position, now, plan_id, position),
                )


def _json(value: Any) -> str:
    # One spelling for one value, so an unchanged group or rule compares equal.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan(connection: sqlite3.Connection, row: sqlite3.Row) -> Plan:
    plan_id = row["id"]
    groups = tuple(
        PlanGroup(
            id=g["id"],
            kind=g["kind"],
            type=g["type"],
            name=g["name"],
            conditions=json.loads(g["conditions"]),
            options=json.loads(g["options"]),
            hidden=bool(g["hidden"]),
        )
        for g in connection.execute(
            "SELECT id, kind, type, name, conditions, options, hidden FROM plan_groups "
            "WHERE plan_id = ? AND deleted_at IS NULL ORDER BY position",
            (plan_id,),
        )
    )
    rules = tuple(
        PlanRule(
            id=r["id"],
            kind=r["kind"],
            content=json.loads(r["content"]),
            target=r["target"],
            enabled=bool(r["enabled"]),
        )
        for r in connection.execute(
            "SELECT id, kind, content, target, enabled FROM plan_rules "
            "WHERE plan_id = ? AND deleted_at IS NULL ORDER BY position",
            (plan_id,),
        )
    )
    return Plan(plan_id, row["name"], row["fallback"], groups, rules)
