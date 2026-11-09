"""Windows: remove the user's own link registrations that open a program no longer there
(docs/release.md §6, D45).

The installer registers the install links' schemes (links.SCHEMES) in HKLM, but a key under
the user's ``HKCU\\Software\\Classes`` wins over it. A client removed without cleaning up leaves
one whose command names a program that is gone, and Windows then reports that it cannot find
the application instead of opening Solaris. As the UI starts, running as that user, it reads
each scheme's ``shell\\open\\command`` there and deletes the scheme's whole key only when the
program it names does not exist, so that Solaris's registration in HKLM takes effect. A key
that opens a program still installed is the user's choice and stays; so does one whose
command cannot be read with certainty (a program found on the PATH, a network path, a drive
that is not there, a variable that is not set).

Only Windows' own Qt platform does this; other platforms, and the tests' offscreen one, never
touch the registry. The tests use a fake ``UserClasses``.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import PureWindowsPath
from typing import Protocol

from solaris.desktop.links import SCHEMES

_log = logging.getLogger(__name__)

CLASSES = r"Software\Classes"
COMMAND = r"shell\open\command"
# ExpandEnvironmentStrings' syntax: %NAME%, the name without % or "=".
_VARIABLE = re.compile(r"%([^%=]+)%")
# Characters no file name has; ":" only after the drive.
_NOT_IN_NAMES = frozenset('"<>|*?:')
# Windows' answer to a name no file can have: ERROR_INVALID_NAME, ERROR_BAD_PATHNAME.
_INVALID_NAME = (123, 161)


class UserClasses(Protocol):
    """The current user's ``HKCU\\Software\\Classes``. Both raise OSError on failure."""

    def command(self, scheme: str) -> str | None:
        """The default value of ``<scheme>\\shell\\open\\command``; None without one."""
        ...

    def delete(self, scheme: str) -> None:
        """Delete ``<scheme>`` with everything under it."""
        ...


class WindowsUserClasses:
    """The real registry, through winreg."""

    def command(self, scheme: str) -> str | None:
        if sys.platform != "win32":
            raise OSError("the registry is Windows'")
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{scheme}\{COMMAND}") as key:
                value, kind = winreg.QueryValueEx(key, "")
        except FileNotFoundError:
            return None
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            return ""  # no command Windows runs; "" is one that cannot be read
        return str(value)

    def delete(self, scheme: str) -> None:
        if sys.platform != "win32":
            raise OSError("the registry is Windows'")
        import winreg

        def delete_tree(path: str) -> None:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                children = []
                while True:
                    try:
                        children.append(winreg.EnumKey(key, len(children)))
                    except OSError:  # ERROR_NO_MORE_ITEMS
                        break
            for child in children:
                delete_tree(rf"{path}\{child}")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)

        delete_tree(rf"{CLASSES}\{scheme}")


def expand(text: str, environ: Mapping[str, str]) -> str:
    """``%NAME%`` replaced as Windows does, ignoring case; an unset one stays as it is."""
    variables = {name.upper(): value for name, value in environ.items()}
    return _VARIABLE.sub(lambda match: variables.get(match[1].upper(), match[0]), text)


def candidates(command: str, environ: Mapping[str, str]) -> list[str] | None:
    """The paths the program in ``command`` may be at, as CreateProcess looks for it; None
    when that cannot be told with certainty.

    A quoted program ends at the closing quote. Without quotes CreateProcess tries each
    stretch up to a space in turn, so ``C:\\Program Files\\A B\\a.exe %1`` may be
    ``C:\\Program``, ``C:\\Program Files\\A``, ... ; a name without an extension gets ``.exe``.
    Environment variables are expanded first, for REG_EXPAND_SZ and REG_SZ alike: of the two
    readings, that one keeps more registrations."""
    text = expand(command, environ).strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            return None
        stretches = [text[1:end]]
    else:
        words = text.split(" ")
        stretches = [" ".join(words[: count + 1]) for count in range(len(words))]
    # Only a full local path: a bare name is looked up on the PATH, a UNC path may only be
    # out of reach, and an unset variable names no file.
    if not _local_path(stretches[0]) or "%" in stretches[0]:
        return None
    paths: list[str] = []
    for stretch in stretches:
        if not _local_path(stretch):
            break  # the longer ones have the same character
        paths.append(stretch)
        if not PureWindowsPath(stretch).suffix:
            paths.append(f"{stretch}.exe")
    return paths


def _local_path(text: str) -> bool:
    """Whether ``text`` is a full path on a drive that a file can have."""
    path = PureWindowsPath(text)
    return (
        bool(path.drive and path.root)
        and not text.startswith("\\\\")
        and not _NOT_IN_NAMES & set(text[len(path.drive) :])
    )


def exists(path: str) -> bool:
    """False only when the file is known not to be there; other failures raise OSError."""
    try:
        os.stat(path)
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError as error:
        if getattr(error, "winerror", None) in _INVALID_NAME:
            return False
        raise
    return True


def is_stale(
    command: str, environ: Mapping[str, str], exists: Callable[[str], bool]
) -> bool | None:
    """Whether ``command`` names a program that is not there; None when that is unknown."""
    paths = candidates(command, environ)
    if paths is None:
        return None
    # The drive itself missing (a removable or mapped one) says nothing about the program.
    if not exists(PureWindowsPath(paths[0]).anchor):
        return None
    return not any(exists(path) for path in paths)


def clear_stale(
    classes: UserClasses,
    environ: Mapping[str, str],
    exists: Callable[[str], bool] = exists,
    schemes: Sequence[str] = SCHEMES,
) -> list[str]:
    """Deletes the schemes whose command names a missing program; returns them."""
    cleared: list[str] = []
    for scheme in schemes:
        try:
            command = classes.command(scheme)
            if command is None:
                continue
            stale = is_stale(command, environ, exists)
            if stale is None:
                _log.warning(
                    "left the user's %s: link registration alone: cannot tell which program "
                    "it opens (%r)",
                    scheme,
                    command,
                )
                continue
            if not stale:
                continue
            classes.delete(scheme)
        except OSError as error:
            _log.warning("could not check the user's %s: link registration: %s", scheme, error)
            continue
        _log.warning(
            "removed the user's %s: link registration, which opened a program that no longer "
            "exists (%r); Solaris's registration applies now",
            scheme,
            command,
        )
        cleared.append(scheme)
    return cleared


def clear_stale_in_background(platform_name: str) -> threading.Thread | None:
    """On Windows' Qt platform, ``clear_stale`` on the user's registry off the Qt thread: a
    path on a drive that sleeps or is out of reach can take a while to answer.
    ``platform_name`` is QGuiApplication.platformName()."""
    if sys.platform != "win32" or platform_name != "windows":
        return None
    thread = threading.Thread(
        target=clear_stale,
        args=(WindowsUserClasses(), os.environ),
        name="clear-stale-link-registrations",
        daemon=True,
    )
    thread.start()
    return thread
