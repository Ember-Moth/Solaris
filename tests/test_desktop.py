"""Desktop integration with doubles only: nothing here registers a login item or a task."""

import asyncio
import ctypes
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from solaris.desktop import session
from solaris.desktop.dock import ACCESSORY, QT_NO_FOREGROUND_ENV, REGULAR, open_in_menu_bar
from solaris.desktop.instance import (
    CONNECT_TIMEOUT_MS,
    LOCK_NAME,
    SingleInstance,
    ask_to_quit,
    server_name,
)
from solaris.desktop.login import (
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
from solaris.desktop.titlebar import ObjC
from solaris.desktop.tray import TRAY_DIR, tray_icon, tray_supported
from solaris.host.base import HostError, HostStatus
from solaris.host.service import ServiceHost
from solaris.host.transport import Opener
from solaris.models.settings import DatabaseSettingsStore
from solaris.models.store import ProfileStore, open_database
from solaris.protocol import control_pb2 as pb
from solaris.viewmodels import desktop as desktop_view_model
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.desktop import DesktopViewModel
from tests.support.dock import ACTIVATE, RecordingApplication
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.test_control_client import Answer, serve

EXE = Path(r"C:\Program Files\Solaris\Solaris.exe")


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
    assert task.name == "Solaris (DESKTOP-1_ember)"
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
from solaris.desktop.instance import SingleInstance
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


# ``--quit`` in a process of its own: it waits for the first UI to let go of the lock,
# which the test does in this thread.
QUITTER = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from solaris.desktop.instance import ask_to_quit
app = QCoreApplication(sys.argv)
sys.exit(0 if ask_to_quit(Path(sys.argv[1]), int(sys.argv[2])) else 1)
"""


def _quitter(directory: Path, timeout_ms: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", QUITTER, str(directory), str(timeout_ms)], text=True
    )


def test_quit_asks_the_first_and_waits_until_it_is_gone(qtbot: QtBot, tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    assert first.claim()
    quitter = _quitter(tmp_path, 30_000)
    try:
        with qtbot.waitSignal(first.quit_requested, timeout=10_000):
            pass
        time.sleep(0.5)
        assert quitter.poll() is None  # the first still holds the lock
        first.release()  # as the UI does last when it exits
        assert quitter.wait(timeout=10) == 0
    finally:
        quitter.kill()
        quitter.wait()
        first.release()
    assert not (tmp_path / LOCK_NAME).exists()


def test_quit_gives_up_on_a_ui_that_stays(qtbot: QtBot, tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    assert first.claim()
    quitter = _quitter(tmp_path, 1500)
    try:
        with qtbot.waitSignal(first.quit_requested, timeout=10_000):
            pass
        assert quitter.wait(timeout=10) == 1
    finally:
        quitter.kill()
        quitter.wait()
        first.release()


# A UI that took the lock and died without letting go of it.
CRASHED_UI = """
import os
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from solaris.desktop.instance import SingleInstance
app = QCoreApplication(sys.argv)
instance = SingleInstance(Path(sys.argv[1]))  # kept: QLockFile unlocks when collected
assert instance.claim()
os._exit(0)
"""


def test_quit_without_a_ui_returns_at_once(tmp_path: Path) -> None:
    never = tmp_path / "never"
    assert ask_to_quit(never, 0)
    assert not never.exists()  # nothing created for a UI that never ran

    subprocess.run([sys.executable, "-c", CRASHED_UI, str(tmp_path)], check=True)
    assert (tmp_path / LOCK_NAME).exists()
    started = time.monotonic()
    assert ask_to_quit(tmp_path)
    assert time.monotonic() - started < CONNECT_TIMEOUT_MS / 1000  # it asked nobody
    assert not (tmp_path / LOCK_NAME).exists()


def test_instances_are_per_user_and_directory(tmp_path: Path) -> None:
    name = server_name(tmp_path / "a", "ember")
    assert name.startswith("solaris-")
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


# -- macOS: the Dock while the window shows, the menu bar only while it hides -------------------
def test_solaris_leaves_the_dock_while_the_window_hides(
    qapp: QCoreApplication, tmp_path: Path
) -> None:
    settings = DatabaseSettingsStore(open_database(tmp_path))
    application = RecordingApplication()
    desktop = DesktopViewModel(
        settings, tray_available=True, platform="darwin", application=application
    )
    desktop.start()
    assert application.calls == []  # opened as usual: the window shows, Solaris in the Dock
    desktop.setWindowShown(False)
    assert application.calls == [ACCESSORY]
    application.calls.clear()
    desktop.setWindowShown(True)
    assert application.calls == [REGULAR, ACTIVATE]  # back in the Dock first, then in front
    desktop.stop()


def test_opened_at_login_solaris_is_in_the_menu_bar_only(
    qapp: QCoreApplication, tmp_path: Path
) -> None:
    settings = DatabaseSettingsStore(open_database(tmp_path))
    application = RecordingApplication()
    desktop = DesktopViewModel(
        settings,
        tray_available=True,
        start_hidden=True,
        platform="darwin",
        application=application,
    )
    assert application.calls == []
    desktop.start()  # before the event loop: the Dock icon goes as soon as it can
    assert application.calls == [ACCESSORY]
    desktop.stop()


def test_opened_into_the_menu_bar_qt_does_not_activate_solaris() -> None:
    """Qt's own activation as it finishes launching would show the window (reopened)."""
    environ: dict[str, str] = {}
    open_in_menu_bar(environ, platform="win32")
    assert environ == {}
    open_in_menu_bar(environ, platform="darwin")
    assert environ == {QT_NO_FOREGROUND_ENV: "1"}


@pytest.mark.parametrize(("platform", "tray"), [("win32", True), ("darwin", False)])
def test_the_dock_stays_as_it_is(
    qapp: QCoreApplication, tmp_path: Path, platform: str, tray: bool
) -> None:
    """Windows has no Dock. Without a tray hiding the window quits Solaris, which keeps its
    Dock icon until it is gone."""
    settings = DatabaseSettingsStore(open_database(tmp_path))
    application = RecordingApplication()
    desktop = DesktopViewModel(
        settings,
        tray_available=tray,
        start_hidden=True,
        platform=platform,
        application=application,
    )
    desktop.start()
    desktop.setWindowShown(False)
    desktop.setWindowShown(True)
    desktop.stop()
    assert application.calls == []


def test_only_the_cocoa_platform_calls_nsapp(
    qapp: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tests' offscreen platform makes no platform calls, even on a Mac."""
    assert QGuiApplication.platformName() == "offscreen"

    def refuse() -> None:
        raise AssertionError("NSApp was asked")

    monkeypatch.setattr(desktop_view_model, "MacApplication", refuse)
    settings = DatabaseSettingsStore(open_database(tmp_path))
    desktop = DesktopViewModel(settings, tray_available=True, start_hidden=True, platform="darwin")
    desktop.start()
    desktop.setWindowShown(True)
    desktop.setWindowShown(False)
    desktop.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="asks the Objective-C runtime")
def test_nsapplication_answers_what_the_dock_asks() -> None:
    """Asked, not called: in the test process the calls would put Python in the Dock."""
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    runtime = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    runtime.sel_registerName.restype = ctypes.c_void_p
    runtime.sel_registerName.argtypes = [ctypes.c_char_p]
    objc = ObjC()
    with objc.pool():
        application = objc.cls(b"NSApplication")
        assert application

        def answers(question: bytes, selector: bytes) -> bool:
            return objc.send(
                ctypes.c_bool,
                application,
                question,
                runtime.sel_registerName(selector),
                argtypes=(ctypes.c_void_p,),
            )

        assert answers(b"respondsToSelector:", b"sharedApplication")
        for selector in (b"setActivationPolicy:", b"activate"):
            assert answers(b"instancesRespondToSelector:", selector)


# -- quitting Solaris (D41) ---------------------------------------------------------------------
class StoppingHost(SimulatedHost):
    """Counts the disconnects, and fails or holds them when told to."""

    def __init__(self) -> None:
        super().__init__()
        self.stops = 0
        self.stop_error: HostError | None = None
        self.stop_delay_s = 0.0

    async def stop(self) -> None:
        self.stops += 1
        await asyncio.sleep(self.stop_delay_s)
        if self.stop_error is not None:
            raise self.stop_error
        await super().stop()

    def running(self) -> bool:
        return asyncio.run(self.get_status()).state == "running"


@pytest.fixture
def composed(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    built = compose(StoppingHost(), store, SimulatedFetcher())
    built.start()
    qtbot.waitUntil(lambda: built.overview.property("connected"))
    yield built
    built.shutdown()


def stopping_host(view_models: ViewModels) -> StoppingHost:
    host = view_models.host
    assert isinstance(host, StoppingHost)
    return host


def test_quitting_disconnects_then_leaves(qtbot: QtBot, composed: ViewModels) -> None:
    desktop, overview, host = composed.desktop, composed.overview, stopping_host(composed)
    stops_when_left: list[int] = []
    desktop.quitRequested.connect(lambda: stops_when_left.append(host.stops))
    with qtbot.waitSignal(desktop.quitRequested):
        desktop.quit()
        assert desktop.property("quitting")
        # The switch shows it going down, as after 断开.
        assert overview.property("connectionChanging")
        assert not overview.property("connectionOn")
        desktop.quit()  # once is enough
    assert stops_when_left == [1]
    assert not host.running()
    assert not desktop.property("quitting")
    desktop.quit()  # it has left already
    assert stops_when_left == [1]


def test_the_installers_quit_leaves_the_tunnel_running(qtbot: QtBot, composed: ViewModels) -> None:
    """``--quit`` closes the UI for the installer, which hands the tunnel over the upgrade
    through the service (docs/architecture.md §9)."""
    desktop, host = composed.desktop, stopping_host(composed)
    with qtbot.waitSignal(desktop.quitRequested, timeout=0):
        desktop.leave()
    assert host.stops == 0
    assert host.running()


@pytest.mark.parametrize("why", ["stopped", "owned_by_other_user", "service_unavailable"])
def test_quitting_without_a_tunnel_of_ones_own_leaves_at_once(
    qtbot: QtBot, composed: ViewModels, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """Another user's tunnel (D5b) is not this user's to stop; with no service answering,
    no tunnel runs in it."""
    desktop, overview, host = composed.desktop, composed.overview, stopping_host(composed)
    if why == "stopped":
        overview.setConnected(False)
        qtbot.waitUntil(lambda: not overview.property("connectionOn"))
        qtbot.waitUntil(lambda: not overview.property("busy"))
    else:

        async def refused() -> HostStatus:
            raise HostError(why, why)

        monkeypatch.setattr(host, "get_status", refused)
        qtbot.waitUntil(lambda: not overview.property("connected"))
    stops = host.stops
    with qtbot.waitSignal(desktop.quitRequested, timeout=0):
        desktop.quit()
    assert host.stops == stops


@pytest.mark.parametrize("answer", ["retry", "anyway", "dismiss"])
def test_a_failed_disconnect_asks_before_quitting(
    qtbot: QtBot, composed: ViewModels, answer: str
) -> None:
    desktop, overview, host = composed.desktop, composed.overview, stopping_host(composed)
    host.stop_error = HostError("core_unresponsive", "the core did not answer")
    with qtbot.waitSignal(desktop.quitFailed):  # the window shows itself and asks
        desktop.quit()
    assert not desktop.property("quitting")
    message = "内核没有响应，请稍后重试。仍然退出时，隧道可能仍在运行。"  # noqa: RUF001 - the UI's copy
    assert desktop.property("quitMessage") == message
    assert overview.property("connectionFailed")
    assert host.running()
    # Asking again shows the window and the question again; nothing is tried yet.
    with qtbot.waitSignal(desktop.showWindowRequested, timeout=0):
        desktop.quit()
    assert host.stops == 1

    host.stop_error = None
    if answer == "retry":
        with qtbot.waitSignal(desktop.quitRequested):
            desktop.retryQuit()
        assert host.stops == 2
        assert not host.running()
    elif answer == "anyway":
        with qtbot.waitSignal(desktop.quitRequested, timeout=0):
            desktop.leave()
        assert host.stops == 1
        assert host.running()
    else:
        with qtbot.assertNotEmitted(desktop.quitRequested, wait=300):
            desktop.cancelQuit()
        assert desktop.property("quitMessage") == ""
        assert host.running()
        # The next quit tries again.
        with qtbot.waitSignal(desktop.quitRequested):
            desktop.quit()
        assert host.stops == 2


def test_a_disconnect_that_does_not_end_is_given_up(
    qtbot: QtBot, composed: ViewModels, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_view_model, "QUIT_DISCONNECT_TIMEOUT_S", 0.2)
    desktop, host = composed.desktop, stopping_host(composed)
    host.stop_delay_s = 30.0
    started = time.monotonic()
    with qtbot.waitSignal(desktop.quitFailed):
        desktop.quit()
    assert time.monotonic() - started < 5
    assert desktop.property("quitMessage").startswith("服务没有响应。")
    assert host.running()


def test_quitting_cancels_wake_recovery(qtbot: QtBot, composed: ViewModels) -> None:
    """The tunnel is down while the service waits to bring it back after sleep (D34);
    quitting disconnects all the same, so it does not come back."""
    desktop, overview, host = composed.desktop, composed.overview, stopping_host(composed)
    host._running = False
    host.recovery_state = "sleeping"
    overview.refresh()
    qtbot.waitUntil(lambda: overview.property("recovering"))
    with qtbot.waitSignal(desktop.quitRequested):
        desktop.quit()
    assert host.stops == 1
    assert host.recovery_state == "none"


@contextmanager
def fake_service(answer: Answer) -> Iterator[Opener]:
    """``serve`` on a loop of its own, for a ServiceHost on the app's runner."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server, opener = asyncio.run_coroutine_threadsafe(serve(answer), loop).result(10)
    try:
        yield opener
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(server.close_clients)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()


def test_quitting_sends_the_service_the_stop_that_disconnect_sends(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Stop is what makes the Windows service keep the tunnel down: it clears was_running and
    cancels wake recovery (owner/service_test.go TestStopIntentIsKeptBeforeTheCoreCanStop,
    owner/power_test.go TestUserDisconnectCancelsPendingWakeRecovery)."""
    methods: list[str] = []
    recovery = pb.RECOVERY_STATE_SLEEPING

    async def answer(request: pb.Request) -> pb.Response:
        nonlocal recovery
        method = request.WhichOneof("method") or ""
        methods.append(method)
        if method == "get_status":
            status = pb.GetStatusResponse(state=pb.RUN_STATE_STOPPED, recovery_state=recovery)
            return pb.Response(get_status=status)
        if method == "stop":
            recovery = pb.RECOVERY_STATE_NONE
        return pb.Response(empty=pb.Empty())

    with fake_service(answer) as opener:
        built = compose(ServiceHost(opener), ProfileStore(tmp_path), SimulatedFetcher())
        built.start()
        try:
            qtbot.waitUntil(lambda: built.overview.property("recovering"))
            assert "stop" not in methods
            with qtbot.waitSignal(built.desktop.quitRequested):
                built.desktop.quit()
            assert methods.count("stop") == 1
        finally:
            built.shutdown()


def test_only_the_users_quit_disconnects() -> None:
    """Qt's Quit events are the system's at the end of the session, except on macOS, where
    only the system's quit Apple Event gives a reason; outside one there is none."""
    assert session.session_ending("win32")
    assert session.session_ending("linux")
    assert session.KEY_QUIT_REASON == 0x7768793F  # 'why?'
    if sys.platform == "darwin":
        assert session.mac_quit_reason() is None
        assert not session.session_ending()


@pytest.mark.skipif(sys.platform != "darwin", reason="reads Apple Events")
def test_the_reason_a_quit_apple_event_gives() -> None:
    def code(text: bytes) -> int:
        return int.from_bytes(text)

    objc = ObjC()
    with objc.pool():
        descriptor = objc.cls(b"NSAppleEventDescriptor")
        event = objc.send(
            ctypes.c_void_p,
            descriptor,
            b"appleEventWithEventClass:eventID:targetDescriptor:returnID:transactionID:",
            code(b"aevt"),
            code(b"quit"),
            None,
            -1,  # kAutoGenerateReturnID
            0,  # kAnyTransactionID
            argtypes=(
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_int16,
                ctypes.c_int32,
            ),
        )
        assert event
        assert session.quit_reason(objc, event) is None  # ⌘Q and the Dock's Quit
        for make in (b"descriptorWithEnumCode:", b"descriptorWithTypeCode:"):
            why = objc.send(
                ctypes.c_void_p, descriptor, make, code(b"rlgo"), argtypes=(ctypes.c_uint32,)
            )
            objc.send(
                None,
                event,
                b"setAttributeDescriptor:forKeyword:",
                why,
                session.KEY_QUIT_REASON,
                argtypes=(ctypes.c_void_p, ctypes.c_uint32),
            )
            assert session.quit_reason(objc, event) == code(b"rlgo")  # kAEReallyLogOut
