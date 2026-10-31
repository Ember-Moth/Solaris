"""The UI's half of an administrator's take-over (D5b): who may, and starting the helper.

UAC's prompt itself only shows on a Windows VM (docs/maintenance.md §2.1); CI's Windows
runners are elevated already, so starting a program elevated asks nothing there.
"""

import os
import sys
import threading
from pathlib import Path

import pytest

from solaris.desktop import elevation
from solaris.host.base import HostError


def test_take_over_needs_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("off Windows only")
    assert not elevation.is_administrator()
    with pytest.raises(HostError) as raised:
        elevation.run_elevated(Path("/bin/true"), "")
    assert raised.value.code == "unsupported_method"


def test_the_helper_is_the_service_beside_the_ui() -> None:
    assert elevation.service_executable() == Path(sys.executable).resolve().parent / (
        "solaris-service.exe"
    )


def test_an_elevated_user_is_an_administrator() -> None:
    if sys.platform != "win32":
        pytest.skip("UAC is on Windows")
    import ctypes

    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("the tests do not run elevated")
    assert elevation.is_administrator()


def test_run_elevated_returns_the_exit_status() -> None:
    if sys.platform != "win32":
        pytest.skip("UAC is on Windows")
    import ctypes

    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip("only an elevated process starts another without UAC's prompt")
    statuses: list[int] = []
    shell = Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
    # A daemon thread, so that a prompt nobody answers fails the test instead of hanging it.
    thread = threading.Thread(
        target=lambda: statuses.append(elevation.run_elevated(shell, "/c exit 7")), daemon=True
    )
    thread.start()
    thread.join(60)
    assert statuses == [7]
