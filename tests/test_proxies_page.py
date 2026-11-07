"""The proxies page: its header stays put while the groups and the current group's nodes
each scroll in their own box, which makes only the rows in sight (DESIGN.md "页面标题栏",
"焦点与键盘")."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtQuick import QQuickItem
from pytestqt.qtbot import QtBot

from solaris.host.base import Proxy, ProxyGroup
from tests.support.window import Loaded, find, find_all, kind

GROUPS = 40
NODES = 500


def group_name(n: int) -> str:
    return f"组 {n:02d}"


def node_name(n: int) -> str:
    return f"节点 {n:03d}"


@pytest.fixture
def page(qtbot: QtBot, loaded: Loaded, monkeypatch: pytest.MonkeyPatch) -> QQuickItem:
    """The proxies page with GROUPS groups of the same NODES nodes, in the default window."""
    view_models = loaded.view_models
    nodes = tuple(Proxy(node_name(n), "Vless", 40 + n) for n in range(NODES))
    groups = [ProxyGroup(group_name(g), "Selector", nodes[g].name, nodes) for g in range(GROUPS)]

    async def list_groups() -> list[ProxyGroup]:
        return groups

    monkeypatch.setattr(view_models.host, "list_groups", list_groups)
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.proxies.refresh()
    view_models.shell.setPage("proxies")
    qtbot.waitUntil(lambda: view_models.proxies.property("nodes").rowCount() == NODES)
    qtbot.wait(100)
    return loaded.item(lambda i: kind(i) == "ProxiesPage")


def lists(page: QQuickItem) -> tuple[QQuickItem, QQuickItem]:
    """The groups' list and the nodes' list."""
    groups, nodes = find_all(page, lambda i: kind(i) == "QQuickListView")
    return groups, nodes


def row(view: QQuickItem, name: str) -> QQuickItem | None:
    """The row the list has made for ``name``, if any."""
    return find(view, lambda i: i.property("name") == name and i.activeFocusOnTab())


def made(view: QQuickItem) -> list[QQuickItem]:
    return find_all(view, lambda i: isinstance(i.property("name"), str) and i.activeFocusOnTab())


def top(item: QQuickItem) -> float:
    return item.mapToScene(QPointF(0, 0)).y()


def offset(view: QQuickItem) -> float:
    """How far the list is scrolled from its first row."""
    return view.property("contentY") - view.property("originY")


def scroll(qtbot: QtBot, view: QQuickItem, to: float) -> None:
    """Scroll the list ``to`` pixels from its first row, or to its end for infinity; the list
    sizes the rows it has not made from those it has, so its end moves while it gets there."""
    for _ in range(5):
        end = view.property("contentHeight") - view.height()
        view.setProperty("contentY", view.property("originY") + min(to, end))
        qtbot.wait(20)
        if to < end or view.property("atYEnd"):
            return


def focused_name(loaded: Loaded) -> str:
    return loaded.focused().property("name")


@pytest.mark.parametrize("size", ["default", "minimum"])
def test_each_box_scrolls_alone_under_a_header_that_stays(
    qtbot: QtBot, loaded: Loaded, page: QQuickItem, size: str
) -> None:
    window = loaded.window
    if size == "minimum":  # the groups stack over the nodes
        window.setWidth(window.minimumWidth())
        window.setHeight(window.minimumHeight())
        qtbot.wait(100)
    groups, nodes = lists(page)
    header = loaded.item(lambda i: kind(i) == "PageHeader")
    titles = loaded.item(lambda i: i.property("text") == "名称")  # the nodes' column titles
    header_top, titles_top = top(header), top(titles)
    # The nodes' box reaches down to the page's bottom margin, and so do the groups' beside it.
    bottom = page.mapToScene(QPointF(0, page.height())).y() - 40 - 1  # inside the border
    assert top(nodes) + nodes.height() == pytest.approx(bottom)
    if size == "default":
        assert top(groups) + groups.height() == pytest.approx(bottom)
    else:
        assert top(groups) + groups.height() < top(nodes)

    # The nodes' box makes the rows in sight, not all five hundred.
    assert 0 < len(made(nodes)) < 40
    first_node = row(nodes, node_name(0))
    assert first_node is not None
    assert loaded.on_screen(first_node)

    # The wheel over the nodes scrolls only them.
    loaded.wheel(qtbot, nodes, 10)
    qtbot.waitUntil(lambda: not nodes.property("moving"))
    assert offset(nodes) > 0
    assert offset(groups) == 0
    assert (top(header), top(titles)) == (header_top, titles_top)
    first_group = row(groups, group_name(0))
    assert first_group is not None
    assert loaded.on_screen(first_group)

    # At the end, the last nodes are made in their place, under the column titles.
    scroll(qtbot, nodes, float("inf"))
    last_node = row(nodes, node_name(NODES - 1))
    assert last_node is not None
    assert loaded.on_screen(last_node)
    assert row(nodes, node_name(0)) is None
    assert 0 < len(made(nodes)) < 40
    assert loaded.on_screen(titles)
    assert top(titles) == titles_top

    # The wheel over the groups scrolls only them.
    before = offset(nodes)
    loaded.wheel(qtbot, groups, 10)
    qtbot.waitUntil(lambda: not groups.property("moving"))
    assert offset(groups) > 0
    assert offset(nodes) == before
    assert (top(header), top(titles)) == (header_top, titles_top)
    scroll(qtbot, groups, float("inf"))
    last_group = row(groups, group_name(GROUPS - 1))
    assert last_group is not None
    assert loaded.on_screen(last_group)
    assert offset(nodes) == before
    assert loaded.warnings == []


def test_the_keyboard_scrolls_the_rows_it_reaches_into_sight(
    qtbot: QtBot, loaded: Loaded, page: QQuickItem
) -> None:
    proxies = loaded.view_models.proxies
    groups, nodes = lists(page)
    test_button = loaded.item(lambda i: kind(i) == "AppButton" and i.property("text") == "测速")

    # Scrolled down and back, the lists hold their rows out of order; Tab still enters the
    # groups at the first one.
    for view in (groups, nodes):
        scroll(qtbot, view, float("inf"))
        scroll(qtbot, view, 0)
    test_button.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert focused_name(loaded) == group_name(0)

    # The arrows scroll the groups to the focused one, ring and all.
    for _ in range(30):
        loaded.key(qtbot, Qt.Key.Key_Down, wait=0)
    qtbot.wait(100)
    focused = loaded.focused()
    assert focused_name(loaded) == group_name(30)
    assert focused.property("visualFocus")
    assert loaded.on_screen(focused)
    assert offset(groups) > 0
    assert offset(nodes) == 0

    # Picking another group shows its nodes from the top, however far they were scrolled.
    scroll(qtbot, nodes, 2000)
    with qtbot.waitSignal(proxies.changed):
        loaded.key(qtbot, Qt.Key.Key_Space, wait=0)
    qtbot.waitUntil(lambda: proxies.property("currentGroup") == group_name(30))
    qtbot.wait(50)
    assert offset(nodes) == 0
    assert loaded.focused() is focused

    # Tab runs through the remaining groups in order, then into the nodes at the first one.
    for n in range(31, GROUPS):
        loaded.key(qtbot, Qt.Key.Key_Tab, wait=0)
        assert focused_name(loaded) == group_name(n)
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert focused_name(loaded) == node_name(0)
    for _ in range(120):
        loaded.key(qtbot, Qt.Key.Key_Down, wait=0)
    qtbot.wait(100)
    assert focused_name(loaded) == node_name(120)
    assert loaded.on_screen(loaded.focused())
    assert loaded.on_screen(loaded.item(lambda i: i.property("text") == "名称"))
    groups_offset = offset(groups)

    # The focused node stays while the list is scrolled away from it, though the list makes
    # its rows from those it scrolls away from; the arrows go on from it.
    scroll(qtbot, nodes, 0)
    scroll(qtbot, nodes, 2000)
    assert focused_name(loaded) == node_name(120)
    assert not loaded.on_screen(loaded.focused())
    loaded.key(qtbot, Qt.Key.Key_Down)
    assert focused_name(loaded) == node_name(121)
    assert loaded.on_screen(loaded.focused())

    # Shift+Tab from the first node goes back to the last group.
    for _ in range(121):
        loaded.key(qtbot, Qt.Key.Key_Up, wait=0)
    qtbot.wait(100)
    assert focused_name(loaded) == node_name(0)
    loaded.key(qtbot, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert focused_name(loaded) == group_name(GROUPS - 1)
    assert offset(groups) == groups_offset  # in sight already

    # Past the last node Tab leaves the page, and Shift+Tab comes back to that node.
    scroll(qtbot, nodes, float("inf"))
    last_node = row(nodes, node_name(NODES - 1))
    assert last_node is not None
    last_node.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert not page.isAncestorOf(loaded.focused())
    loaded.key(qtbot, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert focused_name(loaded) == node_name(NODES - 1)
    assert loaded.on_screen(loaded.focused())
    assert loaded.warnings == []


def test_another_order_shows_the_nodes_from_the_top(
    qtbot: QtBot, loaded: Loaded, page: QQuickItem
) -> None:
    proxies = loaded.view_models.proxies
    _, nodes = lists(page)
    scroll(qtbot, nodes, 2000)
    with qtbot.waitSignal(proxies.changed):
        proxies.setSort("name")
    qtbot.wait(50)
    assert offset(nodes) == 0
    first = row(nodes, node_name(0))
    assert first is not None
    assert loaded.on_screen(first)

    # A refresh in the same group and order leaves the nodes where they are.
    scroll(qtbot, nodes, 2000)
    with qtbot.waitSignal(proxies.changed):
        proxies.refresh()
    qtbot.wait(50)
    assert offset(nodes) == 2000
    assert loaded.warnings == []
