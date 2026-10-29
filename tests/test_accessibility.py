"""Keyboard and screen reader access (DESIGN.md "Components → 焦点", "浮层的键盘行为").

The offscreen platform tabs to every control, as Windows does and macOS does with keyboard
navigation turned on in the system settings.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtQuick import QQuickItem
from pytestqt.qtbot import QtBot

from tests.support.window import Loaded, accessible, find, find_all, kind

PAGES = ["overview", "proxies", "profiles", "connections", "logs", "settings"]
PAGE_TYPES = {
    "overview": "OverviewPage",
    "proxies": "ProxiesPage",
    "profiles": "ProfilesPage",
    "connections": "ConnectionsPage",
    "logs": "LogsPage",
    "settings": "SettingsPage",
}
Role = QAccessible.Role


def show_page(qtbot: QtBot, loaded: Loaded, page: str) -> QQuickItem:
    """Open ``page`` once its data is in, in the smallest window, so that pages scroll."""
    window, view_models = loaded.window, loaded.view_models
    window.setWidth(window.minimumWidth())
    window.setHeight(window.minimumHeight())
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    view_models.shell.setPage(page)
    if page == "proxies":
        qtbot.waitUntil(lambda: view_models.proxies.property("currentGroup") != "")
    if page == "connections":
        qtbot.waitUntil(lambda: view_models.connections.property("total") > 0)
    if page == "logs":
        qtbot.waitUntil(lambda: view_models.logs.property("count") > 0)
    qtbot.wait(100)
    return loaded.item(lambda i: kind(i) == PAGE_TYPES[page])


def nav_item(loaded: Loaded, page: str) -> QQuickItem:
    labels = {p["id"]: p["label"] for p in loaded.view_models.shell.property("pages")}
    return loaded.item(lambda i: kind(i) == "NavItem" and i.property("text") == labels[page])


def tab_cycle(qtbot: QtBot, loaded: Loaded, start: QQuickItem) -> list[QQuickItem]:
    """The controls Tab visits from ``start`` until it comes back to it."""
    start.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    stops = [start]
    for _ in range(200):
        loaded.key(qtbot, Qt.Key.Key_Tab, wait=10)
        focused = loaded.focused()
        if focused is start:
            return stops
        assert focused not in stops, "Tab went round without coming back to the start"
        stops.append(focused)
    raise AssertionError("Tab never came back to the start")


def describe(item: QQuickItem) -> str:
    return f"{kind(item)} {item.property('text')!r}"


@pytest.mark.usefixtures("screen_reader")
@pytest.mark.parametrize("loaded", ["zh", "en"], indirect=True)
@pytest.mark.parametrize("page", PAGES)
def test_tab_reaches_every_control_with_a_name_and_a_ring(
    qtbot: QtBot, loaded: Loaded, page: str
) -> None:
    content = show_page(qtbot, loaded, page)
    stops = tab_cycle(qtbot, loaded, nav_item(loaded, page))

    # Every control of the page is on the way, in the order it is read.
    controls = find_all(content, lambda i: i.activeFocusOnTab() and i.isEnabled())
    missing = [describe(c) for c in controls if c not in stops]
    assert missing == []

    for stop in stops:
        stop.forceActiveFocus(Qt.FocusReason.TabFocusReason)
        qtbot.wait(10)
        info = accessible(stop)
        assert info.name.strip(), f"{describe(stop)} has no accessible name"
        assert info.role not in (Role.NoRole, Role.Client, Role.Pane), describe(stop)
        assert loaded.focus_ring(stop) is not None, f"{describe(stop)} shows no focus ring"
        assert loaded.on_screen(stop), f"{describe(stop)} is scrolled out of view"
    assert loaded.warnings == []


@pytest.mark.usefixtures("screen_reader")
def test_custom_controls_tell_their_role_and_state(qtbot: QtBot, loaded: Loaded) -> None:
    content = show_page(qtbot, loaded, "overview")

    segments = find_all(content, lambda i: accessible(i).role == Role.RadioButton)
    assert [(accessible(s).name, accessible(s).checked) for s in segments] == [
        ("规则", True),
        ("全局", False),
        ("直连", False),
    ]
    group = loaded.item(lambda i: kind(i) == "SegmentedControl")
    assert (accessible(group).role, accessible(group).name) == (Role.Grouping, "模式")

    tun = loaded.item(lambda i: kind(i) == "AppSwitch")
    assert (accessible(tun).role, accessible(tun).name) == (Role.CheckBox, "TUN 模式")
    assert accessible(tun).checked == loaded.view_models.overview.property("tunEnabled")

    chart = loaded.item(lambda i: kind(i) == "TrafficChart")
    assert accessible(chart).role == Role.Graphic
    assert accessible(chart).description.startswith("下载 ")  # the current rates

    usage = loaded.item(lambda i: kind(i) == "UsageBar")
    assert accessible(usage).role == Role.ProgressBar

    title = loaded.item(lambda i: kind(i) == "PageHeader")
    heading = find(title, lambda i: accessible(i).role == Role.Heading)
    assert heading is not None
    status = find(title, lambda i: accessible(i).role == Role.StaticText and i is not heading)
    assert status is not None
    assert accessible(status).name == loaded.view_models.overview.property("statusDetail")

    nav = nav_item(loaded, "overview")
    assert (accessible(nav).role, accessible(nav).selected) == (Role.PageTab, True)

    content = show_page(qtbot, loaded, "settings")
    select = loaded.item(lambda i: kind(i) == "AppSelect")
    assert (accessible(select).role, accessible(select).name) == (Role.ComboBox, "语言")
    assert accessible(select).description == "跟随系统"
    assert loaded.warnings == []


@pytest.mark.usefixtures("screen_reader")
def test_rows_and_menus_are_read_out(qtbot: QtBot, loaded: Loaded) -> None:
    content = show_page(qtbot, loaded, "profiles")
    rows = find_all(content, lambda i: kind(i) == "ProfileRow")
    first = accessible(rows[0])
    assert (first.role, first.name, first.checked) == (Role.RadioButton, "示例订阅 A", True)
    assert first.description.startswith("订阅 · sub.example.com")
    assert "2026-12-31" in first.description

    more = find(rows[1], lambda i: kind(i) == "IconButton")
    assert more is not None
    more.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Space)
    items = loaded.overlay_items(lambda i: kind(i) == "AppMenuItem")
    names = [(accessible(i).role, accessible(i).name) for i in items]
    assert names == [
        (Role.MenuItem, "每 12 小时自动更新"),
        (Role.MenuItem, "复制订阅链接"),
        (Role.MenuItem, "编辑覆写…"),
        (Role.MenuItem, "删除"),
    ]
    assert not accessible(items[0]).checked  # 示例订阅 B is updated by hand
    loaded.key(qtbot, Qt.Key.Key_Escape)

    content = show_page(qtbot, loaded, "connections")
    table = loaded.item(lambda i: kind(i) == "ConnectionsPage")
    box = find(table, lambda i: accessible(i).role == Role.Table)
    assert box is not None
    row = find(table, lambda i: kind(i) == "ConnectionRow")
    assert row is not None
    assert accessible(row).role == Role.Row
    assert accessible(row).description  # rule, chain and rates
    assert loaded.warnings == []


def test_keyboard_alone_imports_uses_and_deletes_a_profile(qtbot: QtBot, loaded: Loaded) -> None:
    view_models = loaded.view_models
    profiles = view_models.profiles
    qtbot.waitUntil(lambda: view_models.overview.property("connected"))
    loaded.window.requestActivate()

    def tab_to(text: str, limit: int = 40, key: Qt.Key = Qt.Key.Key_Tab) -> QQuickItem:
        for _ in range(limit):
            loaded.key(qtbot, key, wait=10)
            if loaded.focused_text() == text:
                return loaded.focused()
        raise AssertionError(f"Tab never reached {text!r}")

    # The sidebar first: Space opens the profiles page.
    tab_to("配置")
    loaded.key(qtbot, Qt.Key.Key_Space)
    assert view_models.shell.property("currentPage") == "profiles"

    # Import from a link: the form opens on the link, Enter imports, and the focus goes back
    # to the button that opened the form, with its ring.
    link_button = tab_to("从链接导入")
    loaded.key(qtbot, Qt.Key.Key_Space)
    assert kind(loaded.focused()) == "AppTextField"
    loaded.type(qtbot, "https://new.example.com/sub")
    loaded.key(qtbot, Qt.Key.Key_Tab)
    loaded.type(qtbot, "键盘订阅")
    with qtbot.waitSignal(profiles.imported):
        loaded.key(qtbot, Qt.Key.Key_Return, wait=0)
    qtbot.waitUntil(lambda: loaded.focused() is link_button)
    assert link_button.property("visualFocus")

    # Into the table: the arrows move between the rows and Space uses the focused one.
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused().property("name") == "示例订阅 A"
    for _ in range(3):
        loaded.key(qtbot, Qt.Key.Key_Down, wait=10)
    row = loaded.focused()
    assert row.property("name") == "键盘订阅"
    with qtbot.waitSignal(profiles.applied):
        loaded.key(qtbot, Qt.Key.Key_Space)
    assert profiles.property("activeName") == "键盘订阅"
    assert loaded.focused() is row  # the table updated in place

    # The profile in use cannot go while the tunnel runs: disconnect on the overview.
    tab_to("概览", key=Qt.Key.Key_Backtab)
    loaded.key(qtbot, Qt.Key.Key_Space)
    tab_to("断开")
    loaded.key(qtbot, Qt.Key.Key_Space)
    qtbot.waitUntil(lambda: not view_models.overview.property("connected"))
    tab_to("配置", key=Qt.Key.Key_Backtab)
    loaded.key(qtbot, Qt.Key.Key_Space)

    # Its menu: the arrows reach 删除 and Enter runs it; the confirmation starts on 取消.
    for _ in range(40):
        loaded.key(qtbot, Qt.Key.Key_Tab, wait=10)
        if accessible_name(loaded.focused()) == "键盘订阅 的更多操作":
            break
    more = loaded.focused()
    assert kind(more) == "IconButton"
    loaded.key(qtbot, Qt.Key.Key_Space)
    for _ in range(4):
        loaded.key(qtbot, Qt.Key.Key_Down, wait=10)
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: loaded.focused_text() == "取消")
    loaded.key(qtbot, Qt.Key.Key_Tab)
    assert loaded.focused_text() == "删除"
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: profiles.property("summary") == "3 个配置")

    # It was the last row: the focus goes to the row above, with its ring.
    qtbot.waitUntil(lambda: loaded.focused().property("name") == "本地配置")
    assert loaded.focused().property("visualFocus")
    assert loaded.warnings == []


def accessible_name(item: QQuickItem) -> str:
    """The name a control states for itself in QML, without turning accessibility on."""
    return str(item.property("accessibleName") or "")


def more_button(loaded: Loaded, name: str) -> QQuickItem:
    page = loaded.item(lambda i: kind(i) == "ProfilesPage")
    row = find(page, lambda i: kind(i) == "ProfileRow" and i.property("name") == name)
    assert row is not None
    more = find(row, lambda i: kind(i) == "IconButton")
    assert more is not None
    return more


def open_menu_item(qtbot: QtBot, loaded: Loaded, more: QQuickItem, downs: int) -> None:
    """Open a row's menu from the keyboard and run its item ``downs`` arrows down."""
    more.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    loaded.key(qtbot, Qt.Key.Key_Space)
    for _ in range(downs):
        loaded.key(qtbot, Qt.Key.Key_Down, wait=10)
    loaded.key(qtbot, Qt.Key.Key_Return)


def test_deleting_a_row_focuses_the_one_that_takes_its_place(qtbot: QtBot, loaded: Loaded) -> None:
    profiles = loaded.view_models.profiles
    show_page(qtbot, loaded, "profiles")
    open_menu_item(qtbot, loaded, more_button(loaded, "示例订阅 B"), 4)  # 删除
    qtbot.waitUntil(lambda: loaded.focused_text() == "取消")
    loaded.key(qtbot, Qt.Key.Key_Tab)
    loaded.key(qtbot, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: profiles.property("summary") == "2 个配置")
    qtbot.waitUntil(lambda: loaded.focused().property("name") == "本地配置")
    assert kind(loaded.focused()) == "ProfileRow"
    assert loaded.focused().property("visualFocus")

    # Cancelling leaves the focus where the menu was opened.
    more = more_button(loaded, "本地配置")
    open_menu_item(qtbot, loaded, more, 2)  # 删除: a local file has two items fewer
    qtbot.waitUntil(lambda: loaded.focused_text() == "取消")
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert loaded.focused() is more
    assert more.property("visualFocus")
    assert loaded.warnings == []


def test_saving_an_override_returns_to_the_rows_more_button(qtbot: QtBot, loaded: Loaded) -> None:
    profiles = loaded.view_models.profiles
    show_page(qtbot, loaded, "profiles")
    more = more_button(loaded, "示例订阅 B")

    def edit() -> None:
        open_menu_item(qtbot, loaded, more, 3)  # 编辑覆写…
        qtbot.waitUntil(lambda: kind(loaded.focused()).startswith("TextArea"))

    # Esc cancels from inside the editor.
    edit()
    loaded.key(qtbot, Qt.Key.Key_Escape)
    assert loaded.focused() is more
    assert more.property("visualFocus")

    edit()
    loaded.type(qtbot, "mode: direct\n")
    with qtbot.waitSignal(profiles.overrideSaved):
        loaded.key(qtbot, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier, wait=0)
    qtbot.waitUntil(lambda: loaded.focused() is more)  # the row was updated, not rebuilt
    assert more.property("visualFocus")
    assert loaded.view_models.profiles.property("model").rows[1].hasOverride
    assert loaded.warnings == []


def test_lists_keep_the_focus_while_they_update(qtbot: QtBot, loaded: Loaded) -> None:
    view_models = loaded.view_models
    show_page(qtbot, loaded, "profiles")
    row = find(
        loaded.item(lambda i: kind(i) == "ProfilesPage"),
        lambda i: kind(i) == "ProfileRow" and i.property("name") == "示例订阅 B",
    )
    assert row is not None
    row.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    view_models.profiles.updateProfile("sample-b")  # rewrites the row twice
    qtbot.waitUntil(lambda: not view_models.profiles.property("model").rows[1].updating)
    assert loaded.focused() is row

    # Selecting a node from the keyboard: the core confirms it and the list updates in place.
    page = show_page(qtbot, loaded, "proxies")
    node = find(page, lambda i: i.property("name") == "日本 01" and i.activeFocusOnTab())
    assert node is not None
    node.forceActiveFocus(Qt.FocusReason.TabFocusReason)
    with qtbot.waitSignal(view_models.proxies.selectionApplied):
        loaded.key(qtbot, Qt.Key.Key_Space, wait=0)
    qtbot.waitUntil(lambda: view_models.overview.property("nodeName") == "日本 01")
    assert loaded.focused() is node
    assert node.property("selected")
    loaded.key(qtbot, Qt.Key.Key_Down)
    assert loaded.focused().property("name") != "日本 01"  # the arrows move between nodes
    assert loaded.warnings == []
