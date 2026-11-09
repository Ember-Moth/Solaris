"""Windows: removing the user's link registrations that open a program no longer there
(docs/release.md §6, D45). The rules run on a fake registry and a fake file system; only the
Windows-only test at the end touches the real registry, with a scratch key of its own."""

from __future__ import annotations

import contextlib
import logging
import sys
import uuid
from types import SimpleNamespace

import pytest

from solaris.desktop import associations
from solaris.desktop.associations import (
    WindowsUserClasses,
    candidates,
    clear_stale,
    clear_stale_in_background,
    is_stale,
)
from solaris.desktop.links import SCHEMES

ENVIRON = {
    "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
    "SystemRoot": r"C:\Windows",
    "ProgramFiles": r"C:\Program Files",
}
FILES = {
    "C:\\",
    "D:\\",
    r"C:\Program Files\Clash Verge\clash-verge.exe",
    r"C:\Users\me\AppData\Local\Programs\FlClash\FlClash.exe",
    r"C:\Tools\clash.exe",
    r"D:\Portable\Meta.exe",
}
GONE = r'"C:\Users\me\AppData\Local\Programs\Clash Party\Clash Party.exe" "%1"'
VALID = r'"C:\Program Files\Clash Verge\clash-verge.exe" "%1"'


def exists(path: str) -> bool:
    """The fake file system: Windows' paths ignore case."""
    return path.lower() in {file.lower() for file in FILES}


class FakeClasses:
    """``HKCU\\Software\\Classes``: each scheme's command, or an error for it."""

    def __init__(self, commands: dict[str, str | OSError]) -> None:
        self.commands = dict(commands)
        self.asked: list[str] = []
        self.deleted: list[str] = []
        self.refuse_delete: set[str] = set()

    def command(self, scheme: str) -> str | None:
        self.asked.append(scheme)
        command = self.commands.get(scheme)
        if isinstance(command, OSError):
            raise command
        return command

    def delete(self, scheme: str) -> None:
        if scheme in self.refuse_delete:
            raise PermissionError(13, "Access is denied")
        self.deleted.append(scheme)
        del self.commands[scheme]


@pytest.mark.parametrize(
    ("command", "paths"),
    [
        # Quoted, with the link after it: the program ends at the quote.
        (GONE, [r"C:\Users\me\AppData\Local\Programs\Clash Party\Clash Party.exe"]),
        (r'"C:\Tools\clash.exe"%1', [r"C:\Tools\clash.exe"]),
        # Without quotes: each stretch up to a space, as CreateProcess tries them.
        (r"C:\Tools\clash.exe %1", [r"C:\Tools\clash.exe", r"C:\Tools\clash.exe %1"]),
        (
            r'C:\Program Files\Old Client\old.exe "%1"',
            [
                r"C:\Program",
                r"C:\Program.exe",
                r"C:\Program Files\Old",
                r"C:\Program Files\Old.exe",
                r"C:\Program Files\Old Client\old.exe",
            ],
        ),
        # A name without an extension: CreateProcess adds .exe.
        (r'"C:\Tools\clash" "%1"', [r"C:\Tools\clash", r"C:\Tools\clash.exe"]),
        # REG_EXPAND_SZ's variables, whatever their case.
        (
            r'"%LocalAppData%\Programs\FlClash\FlClash.exe" "%1"',
            [r"C:\Users\me\AppData\Local\Programs\FlClash\FlClash.exe"],
        ),
        (
            r"%SYSTEMROOT%\clash.exe %1",
            [r"C:\Windows\clash.exe", r"C:\Windows\clash.exe %1"],
        ),
        (r'  "C:\Tools\clash.exe" --url "%1"  ', [r"C:\Tools\clash.exe"]),
    ],
)
def test_where_the_program_may_be(command: str, paths: list[str]) -> None:
    assert candidates(command, ENVIRON) == paths


@pytest.mark.parametrize(
    "command",
    [
        "",
        "   ",
        'clash.exe "%1"',  # found on the PATH
        "rundll32.exe url.dll,FileProtocolHandler %1",
        r'"C:\Tools\clash.exe %1',  # no closing quote
        r'"\\server\share\clash.exe" "%1"',  # perhaps only out of reach
        r'"%UNSET%\clash.exe" "%1"',
        r'"%LOCALAPPDATA" "%1"',
        r"C:clash.exe %1",  # relative to the drive's current folder
        r"\Tools\clash.exe %1",  # relative to the current drive
        r'"C:\Tools\cla*.exe" "%1"',
        r'"C:\Tools\a:b.exe" "%1"',
    ],
)
def test_commands_that_name_no_certain_program(command: str) -> None:
    assert candidates(command, ENVIRON) is None
    assert is_stale(command, ENVIRON, exists) is None


@pytest.mark.parametrize(
    ("command", "stale"),
    [
        (GONE, True),
        (VALID, False),
        (VALID.upper(), False),
        (r"C:\Tools\clash.exe %1", False),
        (r"C:\Tools\gone.exe %1", True),
        (r'"C:\Tools\clash" "%1"', False),
        (r'"%LOCALAPPDATA%\Programs\FlClash\FlClash.exe" "%1"', False),
        (r'"%LOCALAPPDATA%\Programs\Gone\Gone.exe" "%1"', True),
        (r'C:\Program Files\Clash Verge\clash-verge.exe "%1"', False),
        (r'C:\Program Files\Gone Client\gone.exe "%1"', True),
        (r'"D:\Portable\Meta.exe" "%1"', False),
        (r'"D:\Portable\Gone.exe" "%1"', True),
        # The drive is not there (unplugged, not mapped): the program may come back with it.
        (r'"E:\Portable\Meta.exe" "%1"', None),
    ],
)
def test_whether_the_program_is_gone(command: str, stale: bool | None) -> None:
    assert is_stale(command, ENVIRON, exists) is stale


def test_only_registrations_of_a_missing_program_go(caplog: pytest.LogCaptureFixture) -> None:
    classes = FakeClasses({"solaris": r'"C:\Old\Solaris.exe" "%1"', "clash": GONE, "mihomo": VALID})
    with caplog.at_level(logging.INFO, logger=associations.__name__):
        cleared = clear_stale(classes, ENVIRON, exists)
    assert cleared == classes.deleted == ["solaris", "clash"]
    assert classes.asked == list(SCHEMES)
    assert classes.commands == {"mihomo": VALID}  # another client, still installed
    # What went and where it pointed, for the diagnosis.
    removed = [record.getMessage() for record in caplog.records]
    assert len(removed) == 2
    assert "clash:" in removed[1]
    assert repr(GONE) in removed[1]
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_no_registration_of_its_own_is_nothing_to_do(caplog: pytest.LogCaptureFixture) -> None:
    classes = FakeClasses({})
    with caplog.at_level(logging.INFO, logger=associations.__name__):
        assert clear_stale(classes, ENVIRON, exists) == []
    assert classes.asked == list(SCHEMES)
    assert caplog.records == []


def test_a_command_it_cannot_read_stays(caplog: pytest.LogCaptureFixture) -> None:
    classes = FakeClasses({"clash": "rundll32.exe url.dll,FileProtocolHandler %1"})
    with caplog.at_level(logging.INFO, logger=associations.__name__):
        assert clear_stale(classes, ENVIRON, exists) == []
    assert classes.deleted == []
    (record,) = caplog.records
    assert "clash:" in record.getMessage()
    assert "rundll32.exe" in record.getMessage()


def test_failures_leave_that_scheme_and_go_on(caplog: pytest.LogCaptureFixture) -> None:
    classes = FakeClasses(
        {
            "solaris": PermissionError(13, "Access is denied"),
            "clash": GONE,
            "mihomo": r'"C:\Locked\Gone.exe" "%1"',
            "clashmeta": r'"C:\Tools\gone.exe" "%1"',
        }
    )
    classes.refuse_delete.add("clash")

    def locked(path: str) -> bool:
        if path.startswith("C:\\Locked\\"):
            raise PermissionError(13, "Access is denied")
        return exists(path)

    with caplog.at_level(logging.INFO, logger=associations.__name__):
        assert clear_stale(classes, ENVIRON, locked) == ["clashmeta"]
    assert set(classes.commands) == {"solaris", "clash", "mihomo"}
    # One line each: three could not be checked, one went.
    messages = [record.getMessage() for record in caplog.records]
    named = [scheme for message in messages for scheme in SCHEMES if f" {scheme}: " in message]
    assert named == list(SCHEMES)
    assert messages[-1].startswith("removed")


class InvalidNameError(OSError):
    winerror = 123  # ERROR_INVALID_NAME, which Python reports as EINVAL


@pytest.mark.parametrize(
    ("error", "there"),
    [
        (None, True),
        (FileNotFoundError(2, "gone"), False),
        (NotADirectoryError(20, "a file on the way"), False),
        (InvalidNameError(22, "bad name"), False),
        (PermissionError(13, "denied"), None),  # perhaps there: an error
        (OSError(21, "the device is not ready"), None),
    ],
)
def test_the_file_system_answer(
    monkeypatch: pytest.MonkeyPatch, error: OSError | None, there: bool | None
) -> None:
    def stat(path: str) -> None:
        if error is not None:
            raise error

    # The module's os only: the tests' own calls keep the real one.
    monkeypatch.setattr(associations, "os", SimpleNamespace(stat=stat))
    if error is not None and there is None:
        with pytest.raises(type(error), match=error.strerror):
            associations.exists(r"C:\Tools\clash.exe")
    else:
        assert associations.exists(r"C:\Tools\clash.exe") is there


@pytest.mark.parametrize(
    ("platform", "qt_platform", "runs"),
    [
        ("win32", "windows", True),
        ("win32", "offscreen", False),  # the tests' platform on Windows
        ("darwin", "cocoa", False),
        ("linux", "xcb", False),
    ],
)
def test_only_windows_own_platform_looks(
    monkeypatch: pytest.MonkeyPatch, platform: str, qt_platform: str, runs: bool
) -> None:
    classes = FakeClasses({"clash": GONE})
    monkeypatch.setattr(associations, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(associations, "WindowsUserClasses", lambda: classes)
    thread = clear_stale_in_background(qt_platform)
    assert (thread is not None) is runs
    if thread is not None:
        assert thread.daemon
        thread.join(timeout=10)
        assert classes.asked == list(SCHEMES)
    else:
        assert classes.asked == []


@pytest.mark.skipif(sys.platform != "win32", reason="the real registry is Windows'")
def test_the_registry_under_a_scratch_key() -> None:
    assert sys.platform == "win32"
    import winreg

    # Not a URL scheme (no "URL Protocol"), and a name no program uses.
    scheme = f"solaris-test-{uuid.uuid4().hex}"
    path = rf"{associations.CLASSES}\{scheme}"
    classes = WindowsUserClasses()
    try:
        assert classes.command(scheme) is None
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{path}\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "icon")
        assert classes.command(scheme) is None  # a key without a command
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{path}\{associations.COMMAND}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_EXPAND_SZ, GONE)
        assert classes.command(scheme) == GONE
        classes.delete(scheme)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
    finally:
        with contextlib.suppress(FileNotFoundError):
            classes.delete(scheme)
