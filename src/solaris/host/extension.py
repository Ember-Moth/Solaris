"""TunnelHost for the macOS system extension (docs/architecture.md §6): SolarisManager.dylib
brings the tunnel up and down, and carries each control message to the extension as a provider
message (apple/Manager/SolarisManager.h). The library's iCloud calls serve the iCloud backup
(models/backup_icloud.py).

The extension runs only while the tunnel is connected. Until then no core can answer: the
status reads stopped, list queries and polls come back empty, the GEO categories are the
installer's list, and the methods that need the core fail with not_running (§6.3).
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import itertools
import plistlib
import sys
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from google.protobuf.message import DecodeError

from solaris import __version__
from solaris.host.base import (
    TAKEOVER_TUN,
    CompositionReport,
    Connection,
    GeoCategories,
    HostError,
    HostInfo,
    HostStatus,
    LogEntry,
    ProxyGroup,
    Rule,
    RunOptions,
    TrafficSample,
    TunnelConfig,
    bundled_geo_categories,
)
from solaris.host.client import DEFAULT_TIMEOUT_S
from solaris.host.service import ServiceHost
from solaris.host.transport import Streams
from solaris.product import PROTOCOL_VERSION
from solaris.protocol import control_pb2

pb = control_pb2

# SolarisManagerResult
OK = 0
FAILED = 1
TIMED_OUT = 2
NOT_CONFIGURED = 3
NOT_CONNECTED = 4
NEEDS_APPROVAL = 5
NEEDS_REBOOT = 6
NOT_SIGNED_IN = 7
NOT_FOUND = 8
QUOTA_EXCEEDED = 9
UNREACHABLE = 10

# SolarisTunnelStatus: NEVPNStatus, plus a value for having no VPN configuration
TUNNEL_NOT_CONFIGURED = 0
TUNNEL_INVALID = 1
TUNNEL_DISCONNECTED = 2
TUNNEL_CONNECTING = 3
TUNNEL_CONNECTED = 4
TUNNEL_REASSERTING = 5
TUNNEL_DISCONNECTING = 6

LIBRARY_NAME = "SolarisManager.dylib"  # in Solaris.app/Contents/Frameworks
# mihomo's version in the bundled extension, written into the app's Info.plist by
# scripts/build_app.py, for GetInfo while no extension runs to report it.
CORE_VERSION_KEY = "SolarisCoreVersion"

# activate returns at once when the user has to allow the extension.
ACTIVATE_TIMEOUT_S = 60.0
START_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 15.0

# While no extension answers, the run state follows the tunnel's own status. A connected
# tunnel without an answer has not attached the core yet.
_DOWN_STATES = {
    TUNNEL_CONNECTING: "starting",
    TUNNEL_CONNECTED: "starting",
    TUNNEL_REASSERTING: "starting",
    TUNNEL_DISCONNECTING: "stopping",
}
# The host errors of the other results; NOT_CONNECTED and NOT_CONFIGURED become TunnelDownError.
_ERRORS = {
    TIMED_OUT: ("timeout", True),
    NEEDS_APPROVAL: ("extension_needs_approval", True),
    NEEDS_REBOOT: ("extension_needs_reboot", False),
}


class ManagerError(Exception):
    """A SolarisManager call that did not return OK: ``result`` is its SolarisManagerResult."""

    def __init__(self, result: int, message: str) -> None:
        super().__init__(f"{result}: {message}")
        self.result = result
        self.message = message


class Manager(Protocol):
    """The calls of SolarisManager.h. Each blocks until it finishes or times out and raises
    ManagerError unless it returns OK; MacExtensionHost makes them on worker threads."""

    def activate(self, timeout: float) -> None: ...

    def configure(self, include_all_networks: bool) -> None: ...

    def start(self, timeout: float, include_all_networks: bool) -> None:
        """Configure as ``configure`` does, then bring the tunnel up."""
        ...

    def stop(self, timeout: float) -> None: ...

    def status(self) -> int:
        """A TUNNEL_ value."""
        ...

    def send(self, request: bytes, timeout: float) -> bytes:
        """One encoded Request to the extension, its encoded Response back."""
        ...


_ErrorOut = ctypes.POINTER(ctypes.c_void_p)  # char **: kept as a pointer, to free it


class ManagerLibrary:
    """SolarisManager.dylib through ctypes."""

    def __init__(self, path: Path) -> None:
        library = ctypes.CDLL(str(path))
        self._activate = _bind(library, "solaris_manager_activate", ctypes.c_double, _ErrorOut)
        self._configure = _bind(library, "solaris_manager_configure", ctypes.c_bool, _ErrorOut)
        self._start = _bind(
            library, "solaris_manager_start", ctypes.c_double, ctypes.c_bool, _ErrorOut
        )
        self._stop = _bind(library, "solaris_manager_stop", ctypes.c_double, _ErrorOut)
        self._status = _bind(
            library, "solaris_manager_status", ctypes.POINTER(ctypes.c_int32), _ErrorOut
        )
        self._send = _bind(
            library,
            "solaris_manager_send",
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
            _ErrorOut,
        )
        self._icloud_status = _bind(
            library,
            "solaris_manager_icloud_status",
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_int32),
            _ErrorOut,
        )
        self._icloud_save = _bind(
            library,
            "solaris_manager_icloud_save",
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_double,
            _ErrorOut,
        )
        self._icloud_list = _bind(
            library, "solaris_manager_icloud_list", ctypes.c_double, _ErrorOut, _ErrorOut
        )
        self._icloud_download = _bind(
            library,
            "solaris_manager_icloud_download",
            ctypes.c_char_p,
            ctypes.c_double,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_size_t),
            _ErrorOut,
        )
        self._icloud_delete = _bind(
            library, "solaris_manager_icloud_delete", ctypes.c_char_p, ctypes.c_double, _ErrorOut
        )
        self._free = library.solaris_manager_free
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def activate(self, timeout: float) -> None:
        self._call(self._activate, timeout)

    def configure(self, include_all_networks: bool) -> None:
        self._call(self._configure, include_all_networks)

    def start(self, timeout: float, include_all_networks: bool) -> None:
        self._call(self._start, timeout, include_all_networks)

    def stop(self, timeout: float) -> None:
        self._call(self._stop, timeout)

    def status(self) -> int:
        status = ctypes.c_int32()
        self._call(self._status, ctypes.byref(status))
        return status.value

    def send(self, request: bytes, timeout: float) -> bytes:
        response = ctypes.c_void_p()
        length = ctypes.c_size_t()
        try:
            self._call(
                self._send,
                request,
                len(request),
                timeout,
                ctypes.byref(response),
                ctypes.byref(length),
            )
            if not response.value:
                raise ManagerError(FAILED, "the manager returned no response buffer")
            return ctypes.string_at(response.value, length.value)
        finally:
            # An empty Response is still a buffer to release.
            if response.value:
                self._free(response)

    # The iCloud calls (models/backup_icloud.py ICloud).
    def icloud_status(self, timeout: float) -> int:
        status = ctypes.c_int32()
        self._call(self._icloud_status, timeout, ctypes.byref(status))
        return status.value

    def icloud_save(self, name: str, info: str, snapshot: bytes, timeout: float) -> None:
        self._call(
            self._icloud_save, name.encode(), info.encode(), snapshot, len(snapshot), timeout
        )

    def icloud_list(self, timeout: float) -> str:
        listed = ctypes.c_void_p()
        try:
            self._call(self._icloud_list, timeout, ctypes.byref(listed))
            if not listed.value:
                raise ManagerError(FAILED, "the manager returned no list")
            return ctypes.string_at(listed.value).decode("utf-8", "replace")
        finally:
            if listed.value:
                self._free(listed)

    def icloud_download(self, name: str, timeout: float) -> bytes:
        snapshot = ctypes.c_void_p()
        length = ctypes.c_size_t()
        try:
            self._call(
                self._icloud_download,
                name.encode(),
                timeout,
                ctypes.byref(snapshot),
                ctypes.byref(length),
            )
            if not snapshot.value:
                raise ManagerError(FAILED, "the manager returned no snapshot buffer")
            return ctypes.string_at(snapshot.value, length.value)
        finally:
            if snapshot.value:
                self._free(snapshot)

    def icloud_delete(self, name: str, timeout: float) -> None:
        self._call(self._icloud_delete, name.encode(), timeout)

    def _call(self, function: Any, *arguments: object) -> None:
        error = ctypes.c_void_p()
        result = function(*arguments, ctypes.byref(error))
        message = ""
        if error.value:
            message = ctypes.string_at(error.value).decode("utf-8", "replace")
            self._free(error)
        if result != OK:
            raise ManagerError(result, message)


def _bind(library: ctypes.CDLL, name: str, *argtypes: Any) -> Any:
    function = getattr(library, name)
    function.argtypes = list(argtypes)
    function.restype = ctypes.c_int32
    return function


async def _blocking[T](call: Callable[[], T]) -> T:
    """Run a blocking manager call on a thread of its own. NetworkExtension completes on the
    main queue, which nothing serves once the app quits; a call in flight then returns only
    at its timeout, so the thread is a daemon that does not hold the process up."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def settle(outcome: Callable[[], None]) -> None:
        if not future.done():  # the caller may have given up
            outcome()

    def run() -> None:
        try:
            result = call()
        except BaseException as error:  # handed to the caller
            outcome = partial(future.set_exception, error)
        else:
            outcome = partial(future.set_result, result)
        with contextlib.suppress(RuntimeError):  # the loop closed meanwhile
            loop.call_soon_threadsafe(settle, outcome)

    threading.Thread(target=run, name="solaris-manager", daemon=True).start()
    return await future


class TunnelDownError(HostError):
    """The tunnel is not connected, so no extension runs to answer."""

    def __init__(self, message: str) -> None:
        super().__init__("not_running", message)


def _host_error(error: ManagerError, failed: str) -> HostError:
    """The host error of a manager result; ``failed`` is the code for FAILED."""
    if error.result in (NOT_CONNECTED, NOT_CONFIGURED):
        return TunnelDownError(error.message)
    code, retryable = _ERRORS.get(error.result, (failed, True))
    return HostError(code, error.message, retryable=retryable)


async def _no_stream() -> Streams:
    raise AssertionError("MacExtensionHost sends provider messages, not frames")


class MacExtensionHost(ServiceHost):
    """ServiceHost's requests, each sent to the extension with one solaris_manager_send rather
    than over a framed stream; the tunnel's start and stop go through the manager, and the
    configuration follows in Start and Reload (D7). Closing the host leaves the tunnel up.
    The extension only runs while its tunnel is up, so every config runs with TUN, and
    without the system proxy, which only the Windows service sets (D26)."""

    def __init__(self, manager: Manager, *, core_version: str = "") -> None:
        super().__init__(_no_stream, takeover=TAKEOVER_TUN)
        self._manager = manager
        self._core_version = core_version
        self._ids = itertools.count(1)
        self._activated = False
        self._answered = False  # the extension answered the last request
        self._prepared = False  # the tunnel is up for a Start to follow

    @property
    def core_available(self) -> bool:
        return self._answered

    async def _call(self, request: pb.Request, timeout: float = DEFAULT_TIMEOUT_S) -> pb.Response:
        request.protocol_version = PROTOCOL_VERSION
        request.id = next(self._ids)
        send = partial(self._manager.send, request.SerializeToString(), timeout)
        try:
            payload = await _blocking(send)
        except ManagerError as error:
            failure = _host_error(error, "connection_closed")
            if isinstance(failure, TunnelDownError):
                self._forget_extension()
            raise failure from error
        self._answered = True
        try:
            response = pb.Response.FromString(payload)
        except DecodeError as error:
            raise HostError("connection_closed", str(error), retryable=True) from error
        if response.protocol_version != PROTOCOL_VERSION:
            message = f"the extension speaks protocol version {response.protocol_version}"
            raise HostError("unsupported_protocol_version", message)
        if response.WhichOneof("result") == "error":
            failure = response.error
            # Without an id: the extension runs but has no tunnel attached, while the tunnel
            # comes up or goes down.
            if response.id == 0 and failure.code == "not_running":
                self._forget_extension()
                raise TunnelDownError(failure.message)
            raise HostError(failure.code, failure.message, retryable=failure.retryable)
        if response.id != request.id:
            message = f"response {response.id} to request {request.id}"
            raise HostError("connection_closed", message, retryable=True)
        return response

    def _forget_extension(self) -> None:
        """Drop what lived in an extension that is gone: its subscriptions and connections."""
        self._answered = False
        self._subscriptions.clear()
        self._connections = []

    async def _manage(self, call: Callable[[], None]) -> None:
        try:
            await _blocking(call)
        except ManagerError as error:
            raise _host_error(error, "tunnel_failed") from error

    async def _down_state(self) -> str:
        try:
            status = await _blocking(self._manager.status)
        except ManagerError:
            return "stopped"
        return _DOWN_STATES.get(status, "stopped")

    async def get_info(self) -> HostInfo:
        try:
            info = await super().get_info()
        except TunnelDownError:
            state = await self._down_state()
            return HostInfo(PROTOCOL_VERSION, __version__, self._core_version, state, "unspecified")
        self._core_version = info.core_version
        return info

    async def get_status(self) -> HostStatus:
        try:
            return await super().get_status()
        except TunnelDownError:
            return HostStatus(await self._down_state(), "rule", 0, 0, 0, False, False)

    async def prepare_start(self, options: RunOptions) -> None:
        """Bring the tunnel up, with the VPN configuration including all networks or not as
        the options say (D19), so that the extension answers before Start."""
        if not self._activated:
            await self._manage(partial(self._manager.activate, ACTIVATE_TIMEOUT_S))
            self._activated = True
        include_all_networks = options.include_all_networks
        await self._manage(partial(self._manager.start, START_TIMEOUT_S, include_all_networks))
        self._prepared = self._answered = True

    async def start(self, config: TunnelConfig) -> CompositionReport | None:
        """Bring the tunnel up unless prepare_start did, and send it the configuration. When
        the core refuses it, the tunnel goes down again: traffic must not enter a tunnel with
        no core behind it."""
        if not self._prepared:
            await self.prepare_start(config.options)
        self._prepared = False
        try:
            return await super().start(config)
        except HostError as error:
            if error.code != "already_running":
                with contextlib.suppress(HostError):
                    await self.stop()
            raise

    async def stop(self) -> None:
        """Take the tunnel down; the extension stops the core as it detaches."""
        self._prepared = False
        await self._manage(partial(self._manager.stop, STOP_TIMEOUT_S))
        self._forget_extension()

    async def list_geo_categories(self) -> GeoCategories:
        try:
            return await super().list_geo_categories()
        except TunnelDownError:
            return bundled_geo_categories()

    async def list_groups(self) -> list[ProxyGroup]:
        try:
            return await super().list_groups()
        except TunnelDownError:
            return []

    async def list_rules(self) -> list[Rule]:
        try:
            return await super().list_rules()
        except TunnelDownError:
            return []

    async def poll_traffic(self) -> list[TrafficSample]:
        try:
            return await super().poll_traffic()
        except TunnelDownError:
            return []

    async def poll_connections(self) -> list[Connection]:
        try:
            return await super().poll_connections()
        except TunnelDownError:
            return []

    async def poll_logs(self, level: str) -> list[LogEntry]:
        try:
            return await super().poll_logs(level)
        except TunnelDownError:
            return []


def bundled_host(executable: Path | None = None) -> MacExtensionHost | None:
    """The host of a packaged Solaris.app: the manager in Contents/Frameworks beside the
    executable's Contents/MacOS, with the core version from Info.plist. None when the UI runs
    from source, where the development service stands in (docs/architecture.md §11)."""
    contents = (executable or Path(sys.executable)).resolve().parent.parent
    library = contents / "Frameworks" / LIBRARY_NAME
    if not library.is_file():
        return None
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    core_version = str(info.get(CORE_VERSION_KEY, ""))
    return MacExtensionHost(ManagerLibrary(library), core_version=core_version)
