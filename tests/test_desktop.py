"""Desktop integration with doubles only: nothing here registers a login item or a task."""

import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from pytestqt.qtbot import QtBot

from mihomo_desktop.desktop.instance import CONNECT_TIMEOUT_MS, SingleInstance, server_name
from mihomo_desktop.desktop.login import (
    ENABLED,
    NOT_FOUND,
    NOT_REGISTERED,
    REQUIRES_APPROVAL,
    TASK_NAMESPACE,
    LoginItemError,
    LoginItemStatus,
    MainAppLoginItem,
    ScheduledTask,
    UnavailableLoginItem,
    default_login_item,
    task_enabled,
    task_xml,
)
from mihomo_desktop.desktop.tray import TRAY_DIR, tray_icon, tray_supported
from mihomo_desktop.models.settings import DatabaseSettingsStore
from mihomo_desktop.models.store import open_database
from mihomo_desktop.viewmodels.desktop import DesktopViewModel

EXE = Path(r"C:\Program Files\Mihomo Desktop\mihomo-desktop.exe")


class FakeSchtasks:
    """Answers schtasks the way Windows would, keeping the one task in memory."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.task: str | None = None  # the XML registered
        self.created_from = ""
        self.deny = False

    def __call__(self, args: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(args))
        verb = args[0]
        if verb == "/Query":
            return (0, self.task if "/XML" in args else "found") if self.task else (1, "")
        if self.deny:
            return 1, "ERROR: Access is denied."
        if verb == "/Create":
            path = Path(args[args.index("/XML") + 1])
            self.task = path.read_bytes().decode("utf-16")
            self.created_from = str(path)
            return 0, "SUCCESS"
        if verb == "/Delete":
            self.task = None
            return 0, "SUCCESS"
        raise AssertionError(args)


def test_scheduled_task_round_trip() -> None:
    schtasks = FakeSchtasks()
    task = ScheduledTask(EXE, r"DESKTOP-1\ember", run=schtasks)
    assert task.name == "Mihomo Desktop (DESKTOP-1_ember)"
    assert task.status() == LoginItemStatus(enabled=False)

    assert task.set_enabled(True) == LoginItemStatus(enabled=True)
    assert schtasks.calls[1][:3] == ["/Create", "/TN", task.name]
    assert not Path(schtasks.created_from).exists()  # the temporary definition is gone

    assert task.set_enabled(False) == LoginItemStatus(enabled=False)
    assert ["/Delete", "/TN", task.name, "/F"] in schtasks.calls
    task.set_enabled(False)  # nothing to delete
    assert sum(call[0] == "/Delete" for call in schtasks.calls) == 1

    schtasks.deny = True
    with pytest.raises(LoginItemError, match="Access is denied"):
        task.set_enabled(True)


def test_task_definition() -> None:
    xml = task_xml(EXE, r"DESKTOP-1\ember & co")
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    ns = {"t": TASK_NAMESPACE}
    assert root.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=ns) == (
        r"DESKTOP-1\ember & co"
    )
    principal = root.find("t:Principals/t:Principal", ns)
    assert principal is not None
    assert principal.findtext("t:LogonType", namespaces=ns) == "InteractiveToken"
    assert principal.findtext("t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == str(EXE)
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) == "--background"
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
    # Disabled in Task Scheduler without being deleted.
    assert task_enabled(xml.replace('encoding="UTF-16"', ""))
    disabled = xml.replace("<Enabled>true</Enabled>\n  </Settings>", "x")
    assert disabled != xml
    off = xml.replace(
        "<Priority>5</Priority>\n    <Enabled>true</Enabled>",
        "<Priority>5</Priority>\n    <Enabled>false</Enabled>",
    )
    assert not task_enabled(off.replace('encoding="UTF-16"', ""))


class FakeAppService:
    def __init__(self, status: int = NOT_REGISTERED, *, approval: bool = False) -> None:
        self.state = status
        self.approval = approval
        self.opened = 0

    def status(self) -> int:
        return self.state

    def register(self) -> None:
        self.state = REQUIRES_APPROVAL if self.approval else ENABLED

    def unregister(self) -> None:
        self.state = NOT_REGISTERED

    def open_settings(self) -> None:
        self.opened += 1


def test_main_app_login_item() -> None:
    service = FakeAppService()
    item = MainAppLoginItem(service)
    assert item.set_enabled(True) == LoginItemStatus(enabled=True)
    assert item.set_enabled(False) == LoginItemStatus(enabled=False)

    waiting = MainAppLoginItem(FakeAppService(approval=True))
    assert waiting.set_enabled(True) == LoginItemStatus(enabled=True, needs_approval=True)
    assert waiting.set_enabled(False) == LoginItemStatus(enabled=False)
    assert MainAppLoginItem(FakeAppService(NOT_FOUND)).status() == LoginItemStatus(False)


def test_source_runs_have_no_login_item() -> None:
    item = default_login_item()  # the tests run from source
    assert isinstance(item, UnavailableLoginItem)
    assert not item.available
    with pytest.raises(LoginItemError):
        item.set_enabled(True)


# The second UI runs in a process of its own, as it does for real: on Windows the first
# reads the message only in its event loop, which a second UI in this thread would block.
SECOND_UI = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from mihomo_desktop.desktop.instance import SingleInstance
app = QCoreApplication(sys.argv)
print("claiming", flush=True)
sys.exit(1 if SingleInstance(Path(sys.argv[1])).claim() else 0)
"""


def test_second_instance_shows_the_first(qtbot: QtBot, tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    assert first.claim()
    second = subprocess.Popen(
        [sys.executable, "-c", SECOND_UI, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        with qtbot.waitSignal(first.activated, timeout=5000):
            assert second.stdout is not None
            assert second.stdout.readline() == "claiming\n"
            # The first UI is busy, loading its window say, for longer than a connection
            # attempt waits; the message must still arrive.
            time.sleep(2 * CONNECT_TIMEOUT_MS / 1000)
        assert second.wait(timeout=10) == 0  # it found the first and did not take the lock
    finally:
        second.kill()
        second.wait()
        first.release()

    # Once the first has gone, the next one takes over.
    third = SingleInstance(tmp_path)
    try:
        assert third.claim()
    finally:
        third.release()


def test_instances_are_per_user_and_directory(tmp_path: Path) -> None:
    name = server_name(tmp_path / "a", "ember")
    assert name.startswith("mihomo-desktop-")
    assert name == server_name(tmp_path / "a", "ember")
    assert name != server_name(tmp_path / "a", "other")
    assert name != server_name(tmp_path / "b", "ember")


def test_tray_icons() -> None:
    assert tray_supported("cocoa")
    assert tray_supported("windows")
    assert not tray_supported("offscreen")
    assert tray_icon(template=True, light_taskbar=False, connected=True) == (
        TRAY_DIR / "tray-template-on.svg"
    )
    assert tray_icon(template=False, light_taskbar=True, connected=False).name == (
        "tray-light-taskbar-off.svg"
    )
    for name in ("template", "light-taskbar", "dark-taskbar"):
        for state in ("on", "off"):
            assert (TRAY_DIR / f"tray-{name}-{state}.svg").is_file()


@pytest.mark.parametrize(
    ("tray", "close_action", "hides"),
    [(True, "tray", True), (True, "quit", False), (False, "tray", False)],
)
def test_closing_the_window(
    qapp: QCoreApplication, tmp_path: Path, tray: bool, close_action: str, hides: bool
) -> None:
    settings = DatabaseSettingsStore(open_database(tmp_path))
    settings.update(close_action=close_action)
    desktop = DesktopViewModel(settings, tray_available=tray, start_hidden=True, platform="win32")
    assert desktop.property("hideOnClose") is hides
    assert desktop.property("startHidden") is tray  # only into a tray that exists
    assert desktop.property("showOnTrayClick")
    assert desktop.property("trayIconOn").endswith("-taskbar-on.svg")
    mac = DesktopViewModel(settings, tray_available=tray, platform="darwin")
    assert mac.property("trayIconMask")
    assert not mac.property("showOnTrayClick")
    assert mac.property("trayIconOff").endswith("tray-template-off.svg")
