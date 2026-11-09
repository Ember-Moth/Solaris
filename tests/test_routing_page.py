import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtQuick import QQuickItem
from pytestqt.qtbot import QtBot

from solaris.host.base import ProxyGroup, Rule
from tests.support.window import Loaded, find_all, kind


def top(item: QQuickItem) -> float:
    return item.mapToScene(QPointF()).y()


def offset(view: QQuickItem) -> float:
    return view.property("contentY") - view.property("originY")


@pytest.mark.parametrize("narrow", [False, True], ids=["side-by-side", "stacked"])
def test_profile_groups_and_rules_scroll_independently(
    qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch, narrow: bool
) -> None:
    groups = [ProxyGroup(f"Group {i}", "Selector", "DIRECT", ()) for i in range(40)]
    rules = [Rule("DOMAIN", f"rule-{i:03d}.example", "DIRECT", "") for i in range(500)]
    models = loaded.view_models

    async def list_groups() -> list[ProxyGroup]:
        return groups

    async def list_rules() -> list[Rule]:
        return rules

    monkeypatch.setattr(models.host, "list_groups", list_groups)
    monkeypatch.setattr(models.host, "list_rules", list_rules)
    if narrow:
        loaded.window.setWidth(loaded.window.minimumWidth())
        loaded.window.setHeight(loaded.window.minimumHeight())
    qtbot.waitUntil(lambda: models.overview.property("connected"))
    models.shell.setPage("routing")
    qtbot.waitUntil(lambda: models.routing._profile_routes.property("running"))

    page = loaded.item(lambda item: kind(item) == "RoutingPage")
    group_list, rule_list = find_all(page, lambda item: kind(item) == "QQuickListView")
    qtbot.waitUntil(lambda: rule_list.property("count") == len(rules))
    qtbot.wait(50)
    search = loaded.item(lambda item: kind(item) == "SearchField")
    header = loaded.item(lambda item: item.property("text") == "匹配")
    assert loaded.on_screen(search)
    assert loaded.on_screen(header)
    if narrow:
        assert top(group_list) + group_list.height() < top(rule_list)
    else:
        assert top(group_list) + group_list.height() == pytest.approx(
            top(rule_list) + rule_list.height()
        )
    assert 0 < len(find_all(rule_list, lambda item: bool(item.property("match")))) < 40

    fixed = (top(search), top(header))
    loaded.wheel(qtbot, rule_list, 10)
    qtbot.waitUntil(lambda: not rule_list.property("moving"))
    assert offset(rule_list) > 0
    assert offset(group_list) == 0
    assert (top(search), top(header)) == fixed

    rule_offset = offset(rule_list)
    loaded.wheel(qtbot, group_list, 10)
    qtbot.waitUntil(lambda: not group_list.property("moving"))
    group_offset = offset(group_list)
    assert group_offset > 0
    assert offset(rule_list) == rule_offset
    assert (top(search), top(header)) == fixed

    table = rule_list.parentItem()
    assert table is not None
    assert table.activeFocusOnTab()
    table.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_PageDown)
    assert offset(rule_list) > rule_offset
    assert offset(group_list) == group_offset

    search.forceActiveFocus()
    loaded.type(qtbot, "rule-499")
    qtbot.waitUntil(lambda: rule_list.property("count") == 1)
    assert offset(rule_list) == 0
    assert offset(group_list) == group_offset
    loaded.type(qtbot, "-no-match")
    qtbot.waitUntil(lambda: rule_list.property("count") == 0)
    assert loaded.on_screen(loaded.item(lambda item: item.property("text") == "没有匹配的规则"))
    assert (top(search), top(header)) == fixed
    assert loaded.warnings == []
