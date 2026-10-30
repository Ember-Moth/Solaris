"""Proxy group helpers shared by the overview and proxies screens."""

from __future__ import annotations

from solaris.host.base import Proxy, ProxyGroup

SORTS = ("config", "name", "delay")
_MAX_NESTING = 8


def main_group(groups: list[ProxyGroup]) -> ProxyGroup | None:
    """The first user-selectable group, which is what "the current node" refers to."""
    return next((g for g in groups if g.selectable and g.name != "GLOBAL"), None)


def resolve(groups: list[ProxyGroup], name: str) -> Proxy | None:
    """Follow ``now`` through nested groups down to the proxy that carries traffic."""
    by_name = {g.name: g for g in groups}
    for _ in range(_MAX_NESTING):
        group = by_name.get(name)
        if group is None:
            return None
        name = group.now
        if name not in by_name:
            return next((p for p in group.proxies if p.name == name), None)
    return None


def sort_proxies(proxies: tuple[Proxy, ...], sort: str) -> list[Proxy]:
    if sort == "name":
        return sorted(proxies, key=lambda p: p.name)
    if sort == "delay":
        # Failed and untested proxies go last.
        return sorted(proxies, key=lambda p: (not p.delay_ms, p.delay_ms or 0))
    return list(proxies)
