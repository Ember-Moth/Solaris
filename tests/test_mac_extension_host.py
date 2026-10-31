"""MacExtensionHost over a stand-in manager, and ManagerLibrary over a stand-in dylib built
from SolarisManager.h. Nothing here touches NetworkExtension."""

import asyncio
import ctypes
import plistlib
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from solaris import __version__
from solaris.host import extension
from solaris.host.base import HostError, HostInfo, HostStatus, RunOptions, TunnelConfig
from solaris.host.extension import (
    MacExtensionHost,
    ManagerError,
    ManagerLibrary,
    TunnelDownError,
    bundled_host,
)
from solaris.models import backup_icloud
from solaris.protocol import control_pb2

pb = control_pb2
HEADER = Path(__file__).resolve().parents[1] / "apple" / "Manager" / "SolarisManager.h"

type Answer = Callable[[pb.Request], pb.Response]


def running_status() -> pb.Response:
    return pb.Response(
        get_status=pb.GetStatusResponse(
            state=pb.RUN_STATE_RUNNING, mode=pb.MODE_GLOBAL, tun_enabled=True
        )
    )


class FakeManager:
    """The manager with a tunnel that connects at once; ``answer`` stands in for the core."""

    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.tunnel = extension.TUNNEL_DISCONNECTED
        self.calls: list[str] = []
        self.requests: list[pb.Request] = []
        self.threads: set[threading.Thread] = set()
        self.failures: dict[str, ManagerError] = {}  # by call, raised once
        self.answer_ids = True  # False sends the answer's own id, as for id 0
        self.include_all_networks: list[bool] = []  # what configure and start were given

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self.threads.add(threading.current_thread())
        if name in self.failures:
            raise self.failures.pop(name)

    def activate(self, timeout: float) -> None:
        self._record("activate")

    def deactivate(self, timeout: float) -> None:
        self.tunnel = extension.TUNNEL_DISCONNECTED
        self._record("deactivate")
        self.tunnel = extension.TUNNEL_NOT_CONFIGURED

    def configure(self, include_all_networks: bool) -> None:
        self._record("configure")
        self.include_all_networks.append(include_all_networks)

    def start(self, timeout: float, include_all_networks: bool) -> None:
        self._record("start")
        self.include_all_networks.append(include_all_networks)
        self.tunnel = extension.TUNNEL_CONNECTED

    def stop(self, timeout: float) -> None:
        self._record("stop")
        self.tunnel = extension.TUNNEL_DISCONNECTED

    def status(self) -> int:
        self._record("status")
        return self.tunnel

    def send(self, request: bytes, timeout: float) -> bytes:
        self._record("send")
        if self.tunnel not in (extension.TUNNEL_CONNECTED, extension.TUNNEL_REASSERTING):
            raise ManagerError(extension.NOT_CONNECTED, "The Solaris tunnel is not connected")
        decoded = pb.Request.FromString(request)
        self.requests.append(decoded)
        response = self.answer(decoded)
        response.protocol_version = 1
        if self.answer_ids and not response.id:
            response.id = decoded.id
        return response.SerializeToString()

    def methods(self) -> list[str | None]:
        return [request.WhichOneof("method") for request in self.requests]


def test_provider_message_timeout_does_not_wait_for_a_stuck_native_call() -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    release = threading.Event()
    original = manager.send

    def blocked(request: bytes, timeout: float) -> bytes:
        release.wait(2)
        return original(request, timeout)

    manager.send = blocked
    host = MacExtensionHost(manager)

    async def scenario() -> None:
        try:
            with pytest.raises(HostError) as raised:
                async with asyncio.timeout(1):
                    await host._call(pb.Request(get_info=pb.GetInfoRequest()), timeout=0.02)
            assert raised.value.code == "timeout"
        finally:
            release.set()
            await host.close()

    asyncio.run(scenario())


def test_deactivation_forgets_the_old_tunnel_and_next_connect_activates_again() -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    host = MacExtensionHost(manager)

    async def scenario() -> None:
        config = TunnelConfig("proxies: []\n")
        await host.start(config)
        host._subscriptions["traffic"] = 42
        assert host.core_available
        await host.deactivate_extension()
        assert manager.tunnel == extension.TUNNEL_NOT_CONFIGURED
        assert not host.core_available
        assert host._subscriptions == {}
        await host.start(config)
        assert manager.calls == [
            "activate",
            "start",
            "send",
            "deactivate",
            "activate",
            "start",
            "send",
        ]
        await host.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("result", [extension.NEEDS_APPROVAL, extension.NEEDS_REBOOT])
def test_deactivation_pending_is_not_reported_as_success_and_can_be_retried(result: int) -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    manager.failures["deactivate"] = ManagerError(result, "system request pending")
    host = MacExtensionHost(manager)

    async def scenario() -> None:
        with pytest.raises(HostError) as raised:
            await host.deactivate_extension()
        assert raised.value.code == "extension_deactivation_pending"
        assert raised.value.retryable
        assert not host.core_available
        await host.deactivate_extension()
        assert manager.calls == ["deactivate", "deactivate"]
        assert manager.tunnel == extension.TUNNEL_NOT_CONFIGURED
        await host.close()

    asyncio.run(scenario())


def test_connect_cannot_overtake_deactivation() -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    entered, release = threading.Event(), threading.Event()
    original = manager.deactivate

    def blocked(timeout: float) -> None:
        entered.set()
        release.wait(2)
        original(timeout)

    manager.deactivate = blocked
    host = MacExtensionHost(manager)

    async def scenario() -> None:
        deactivating = asyncio.create_task(host.deactivate_extension())
        assert await asyncio.to_thread(entered.wait, 1)
        connecting = asyncio.create_task(host.start(TunnelConfig("proxies: []\n")))
        try:
            await asyncio.sleep(0.02)
            assert "activate" not in manager.calls
            release.set()
            await asyncio.gather(deactivating, connecting)
            assert manager.calls == ["deactivate", "activate", "start", "send"]
        finally:
            release.set()
            await host.close()

    asyncio.run(scenario())


def test_without_a_tunnel_the_host_reports_stopped_and_empty() -> None:
    manager = FakeManager(lambda _: pb.Response())
    host = MacExtensionHost(manager, core_version="v1.19.33")

    async def scenario() -> None:
        info = HostInfo(1, __version__, "v1.19.33", "stopped", "unspecified")
        assert await host.get_info() == info
        assert await host.get_status() == HostStatus("stopped", "rule", 0, 0, 0, False, False)
        assert await host.list_groups() == []
        assert await host.list_rules() == []
        assert await host.poll_traffic() == []
        assert await host.poll_connections() == []
        assert await host.poll_logs("info") == []
        categories = await host.list_geo_categories()
        assert categories.geosite.bundled
        assert categories.geoip.bundled
        # No core reads a configuration until the tunnel is up.
        config = TunnelConfig("proxies: []\n")
        for call in (
            host.validate(config),
            host.inspect_source(config.config_yaml),
            host.reload(config),
            host.set_mode("global"),
        ):
            with pytest.raises(TunnelDownError) as raised:
                await call
            assert raised.value.code == "not_running"
        await host.close()

    asyncio.run(scenario())
    assert manager.requests == []
    assert "stop" not in manager.calls


def test_a_tunnel_that_comes_up_or_goes_down_reads_as_starting_or_stopping() -> None:
    manager = FakeManager(lambda _: pb.Response())
    host = MacExtensionHost(manager)

    async def states() -> list[str]:
        found = []
        for tunnel in (extension.TUNNEL_CONNECTING, extension.TUNNEL_DISCONNECTING):
            manager.tunnel = tunnel
            found.append((await host.get_status()).state)
        return found

    assert asyncio.run(states()) == ["starting", "stopping"]


def test_start_activates_connects_and_sends_the_config() -> None:
    def answer(request: pb.Request) -> pb.Response:
        if request.HasField("get_status"):
            return running_status()
        if request.HasField("get_info"):
            return pb.Response(
                get_info=pb.GetInfoResponse(
                    protocol_version=1,
                    service_version="1.0.0",
                    core_version="v1.19.33",
                    state=pb.RUN_STATE_RUNNING,
                )
            )
        return pb.Response(empty=pb.Empty())

    manager = FakeManager(answer)
    host = MacExtensionHost(manager)
    # Whatever the settings ask for, a TUN without the system proxy (D26).
    options = RunOptions(tun_enabled=False, system_proxy_enabled=True, tun_stack="gvisor")
    config = TunnelConfig("proxies: []\n", ("mode: rule\n",), options)
    all_networks = replace(config, options=RunOptions(include_all_networks=True))

    async def scenario() -> tuple[HostStatus, HostInfo]:
        assert await host.start(config) is None
        status = await host.get_status()
        info = await host.get_info()
        await host.reload(config)
        await host.stop()
        await host.start(all_networks)
        return status, info

    status, info = asyncio.run(scenario())
    assert (status.state, status.mode, status.tun_enabled) == ("running", "global", True)
    assert info.core_version == "v1.19.33"
    # Activation once per run of the app; the configuration only in Start (D7).
    assert [call for call in manager.calls if call != "send"] == [
        "activate",
        "start",
        "stop",
        "start",
    ]
    start = manager.requests[0].start
    assert (start.config_yaml, list(start.overrides)) == ("proxies: []\n", ["mode: rule\n"])
    assert start.options.tun_stack == pb.TUN_STACK_GVISOR
    reload = manager.requests[3].reload
    for sent in (start.options, reload.options, manager.requests[4].start.options):
        assert (sent.tun_enabled, sent.system_proxy_enabled) == (True, False)
    # The VPN configuration carries the setting; the extension tells the core (D19).
    assert manager.include_all_networks == [False, True]
    assert [request.id for request in manager.requests] == [1, 2, 3, 4, 5]
    assert threading.main_thread() not in manager.threads


def test_a_config_the_core_refuses_takes_the_tunnel_down_again() -> None:
    def answer(_: pb.Request) -> pb.Response:
        return pb.Response(error=pb.Error(code="invalid_config", message="no proxies"))

    manager = FakeManager(answer)
    host = MacExtensionHost(manager)

    with pytest.raises(HostError) as raised:
        asyncio.run(host.start(TunnelConfig("proxies: []\n")))
    assert (raised.value.code, raised.value.message) == ("invalid_config", "no proxies")
    assert [call for call in manager.calls if call != "send"] == ["activate", "start", "stop"]
    assert manager.tunnel == extension.TUNNEL_DISCONNECTED


def test_prepare_start_brings_the_tunnel_up_for_the_core_to_answer() -> None:
    """Profiles stored while the tunnel was down are read between the two (D27)."""
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    host = MacExtensionHost(manager)
    config = TunnelConfig("proxies: []\n", options=RunOptions(include_all_networks=True))

    async def scenario() -> list[bool]:
        available = [host.core_available]
        await host.prepare_start(config.options)
        available.append(host.core_available)
        await host.validate(config)
        await host.start(config)
        available.append(host.core_available)
        await host.stop()
        available.append(host.core_available)
        return available

    assert asyncio.run(scenario()) == [False, True, True, False]
    # The tunnel comes up once; Start follows the reading.
    assert [call for call in manager.calls if call != "send"] == ["activate", "start", "stop"]
    assert manager.methods() == ["validate", "start"]
    assert manager.include_all_networks == [True]


@pytest.mark.parametrize(
    ("result", "code", "retryable"),
    [
        (extension.NEEDS_APPROVAL, "extension_needs_approval", True),
        (extension.NEEDS_REBOOT, "extension_needs_reboot", False),
    ],
)
def test_pending_activation_is_asked_again_on_the_next_start(
    result: int, code: str, retryable: bool
) -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    host = MacExtensionHost(manager)

    for attempt in range(2):
        manager.failures["activate"] = ManagerError(result, "Activation pending")
        with pytest.raises(HostError) as raised:
            asyncio.run(host.start(TunnelConfig("proxies: []\n")))
        assert (raised.value.code, raised.value.retryable) == (code, retryable)
        assert manager.calls == ["activate"] * (attempt + 1)

    asyncio.run(host.start(TunnelConfig("proxies: []\n")))
    assert manager.calls[2:4] == ["activate", "start"]


@pytest.mark.parametrize(
    ("result", "code", "retryable"),
    [
        (extension.FAILED, "tunnel_failed", True),
        (extension.TIMED_OUT, "timeout", True),
        (extension.NEEDS_REBOOT, "extension_needs_reboot", False),
        (extension.BUSY, "extension_busy", True),
        (extension.CANCELLED, "cancelled", False),
    ],
)
def test_lifecycle_failures_become_host_errors(result: int, code: str, retryable: bool) -> None:
    manager = FakeManager(lambda _: pb.Response(empty=pb.Empty()))
    manager.failures["start"] = ManagerError(result, "message from the manager")

    with pytest.raises(HostError) as raised:
        asyncio.run(MacExtensionHost(manager).start(TunnelConfig("proxies: []\n")))
    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    assert raised.value.message == "message from the manager"


@pytest.mark.parametrize(
    ("result", "code"),
    [(extension.FAILED, "connection_closed"), (extension.TIMED_OUT, "timeout")],
)
def test_send_failures_become_host_errors(result: int, code: str) -> None:
    manager = FakeManager(lambda _: running_status())
    manager.tunnel = extension.TUNNEL_CONNECTED
    manager.failures["send"] = ManagerError(result, "no answer")

    with pytest.raises(HostError) as raised:
        asyncio.run(MacExtensionHost(manager).get_status())
    assert (raised.value.code, raised.value.retryable) == (code, True)


def test_an_extension_without_a_tunnel_attached_reads_as_starting() -> None:
    def answer(_: pb.Request) -> pb.Response:
        error = pb.Error(code="not_running", message="the extension has no tunnel attached")
        return pb.Response(error=error)

    manager = FakeManager(answer)
    manager.tunnel = extension.TUNNEL_CONNECTED
    manager.answer_ids = False
    host = MacExtensionHost(manager)

    async def scenario() -> HostStatus:
        with pytest.raises(TunnelDownError):
            await host.reload(TunnelConfig("proxies: []\n"))
        return await host.get_status()

    assert asyncio.run(scenario()).state == "starting"


def test_core_errors_and_mismatched_answers_raise() -> None:
    answers: list[pb.Response] = [
        pb.Response(error=pb.Error(code="not_running", message="core stopped")),
        pb.Response(id=99, get_status=pb.GetStatusResponse()),
    ]
    manager = FakeManager(lambda _: answers.pop(0))
    manager.tunnel = extension.TUNNEL_CONNECTED
    host = MacExtensionHost(manager)

    async def scenario() -> list[str]:
        codes = []
        for _ in range(2):
            try:
                await host.get_status()
            except HostError as error:
                codes.append(error.code)
        return codes

    # The core's own not_running carries the request's id: the tunnel is up, the core is not.
    assert asyncio.run(scenario()) == ["not_running", "connection_closed"]


def test_subscriptions_start_again_after_the_tunnel_went_down() -> None:
    subscription = iter(range(10, 20))

    def answer(request: pb.Request) -> pb.Response:
        if request.HasField("subscribe_traffic"):
            return pb.Response(subscribe=pb.SubscribeResponse(subscription_id=next(subscription)))
        sample = pb.TrafficSample(up_bytes_per_second=1, down_bytes_per_second=2)
        return pb.Response(poll_events=pb.PollEventsResponse(events=[pb.Event(traffic=sample)]))

    manager = FakeManager(answer)
    manager.tunnel = extension.TUNNEL_CONNECTED
    host = MacExtensionHost(manager)

    async def scenario() -> list[int]:
        counts = [len(await host.poll_traffic())]
        manager.tunnel = extension.TUNNEL_DISCONNECTED
        counts.append(len(await host.poll_traffic()))
        manager.tunnel = extension.TUNNEL_CONNECTED
        counts.append(len(await host.poll_traffic()))
        return counts

    assert asyncio.run(scenario()) == [1, 0, 1]
    polled = [r.poll_events.subscription_id for r in manager.requests if r.HasField("poll_events")]
    assert manager.methods().count("subscribe_traffic") == 2
    assert polled == [10, 11]


def test_result_codes_match_the_header() -> None:
    header = HEADER.read_text()
    declared = dict(re.findall(r"^\s*SOLARIS_(\w+) = (\d+),", header, re.MULTILINE))
    for name, value in declared.items():
        module = backup_icloud if name.startswith("ICLOUD_") else extension
        python = name.removeprefix("MANAGER_").removeprefix("ICLOUD_")
        assert getattr(module, python) == int(value), name
    assert len(declared) == 26


FAKE_MANAGER_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "SolarisManager.h"

static int outstanding;
static SolarisManagerResult next_result;
static double last_timeout;
static int last_include_all_networks = -1;

static void *allocate(size_t size) {
  outstanding++;
  return malloc(size ? size : 1);
}

static SolarisManagerResult finish(char **error) {
  SolarisManagerResult result = next_result;
  next_result = SOLARIS_MANAGER_OK;
  *error = NULL;
  if (result != SOLARIS_MANAGER_OK) {
    *error = allocate(32);
    snprintf(*error, 32, "fake failure %d", (int)result);
  }
  return result;
}

void fake_fail_next(SolarisManagerResult result) { next_result = result; }
int fake_outstanding(void) { return outstanding; }
double fake_last_timeout(void) { return last_timeout; }
int fake_last_include_all_networks(void) { return last_include_all_networks; }

SolarisManagerResult solaris_manager_activate(double timeout_seconds, char **error) {
  last_timeout = timeout_seconds;
  return finish(error);
}

SolarisManagerResult solaris_manager_deactivate(double timeout_seconds, char **error) {
  last_timeout = timeout_seconds;
  return finish(error);
}

SolarisManagerResult solaris_manager_configure(bool include_all_networks, char **error) {
  last_include_all_networks = include_all_networks;
  return finish(error);
}

SolarisManagerResult solaris_manager_start(double timeout_seconds, bool include_all_networks,
                                           char **error) {
  last_timeout = timeout_seconds;
  last_include_all_networks = include_all_networks;
  return finish(error);
}

SolarisManagerResult solaris_manager_stop(double timeout_seconds, char **error) {
  last_timeout = timeout_seconds;
  return finish(error);
}

SolarisManagerResult solaris_manager_status(SolarisTunnelStatus *status, char **error) {
  *status = SOLARIS_TUNNEL_REASSERTING;
  return finish(error);
}

// Answers with the request reversed.
SolarisManagerResult solaris_manager_send(const uint8_t *request, size_t request_length,
                                          double timeout_seconds, uint8_t **response,
                                          size_t *response_length, char **error) {
  last_timeout = timeout_seconds;
  *response = NULL;
  *response_length = 0;
  SolarisManagerResult result = finish(error);
  if (result != SOLARIS_MANAGER_OK) return result;
  uint8_t *reply = allocate(request_length);
  for (size_t i = 0; i < request_length; i++) reply[request_length - 1 - i] = request[i];
  *response = reply;
  *response_length = request_length;
  return result;
}

SolarisManagerResult solaris_manager_icloud_status(double timeout_seconds,
                                                   SolarisICloudStatus *status, char **error) {
  last_timeout = timeout_seconds;
  *status = SOLARIS_ICLOUD_NO_ACCOUNT;
  return finish(error);
}

// iCloud holds one snapshot: the last one saved.
static char saved_name[256];
static char saved_info[256];
static uint8_t saved[256];
static size_t saved_length;

const char *fake_saved_info(void) { return saved_info; }

SolarisManagerResult solaris_manager_icloud_save(const char *name, const char *info,
                                                 const uint8_t *snapshot, size_t snapshot_length,
                                                 double timeout_seconds, char **error) {
  last_timeout = timeout_seconds;
  snprintf(saved_name, sizeof saved_name, "%s", name);
  snprintf(saved_info, sizeof saved_info, "%s", info);
  saved_length = snapshot_length < sizeof saved ? snapshot_length : sizeof saved;
  memcpy(saved, snapshot, saved_length);
  return finish(error);
}

SolarisManagerResult solaris_manager_icloud_list(double timeout_seconds, char **list,
                                                 char **error) {
  last_timeout = timeout_seconds;
  *list = NULL;
  SolarisManagerResult result = finish(error);
  if (result != SOLARIS_MANAGER_OK) return result;
  *list = allocate(512);
  snprintf(*list, 512, "[{\"name\":\"%s\",\"info\":\"%zu bytes\"}]", saved_name,
           saved_length);
  return result;
}

SolarisManagerResult solaris_manager_icloud_download(const char *name, double timeout_seconds,
                                                     uint8_t **snapshot, size_t *snapshot_length,
                                                     char **error) {
  last_timeout = timeout_seconds;
  *snapshot = NULL;
  *snapshot_length = 0;
  if (saved_name[0] == 0 || strcmp(name, saved_name) != 0) next_result = SOLARIS_MANAGER_NOT_FOUND;
  SolarisManagerResult result = finish(error);
  if (result != SOLARIS_MANAGER_OK) return result;
  *snapshot = allocate(saved_length);
  memcpy(*snapshot, saved, saved_length);
  *snapshot_length = saved_length;
  return result;
}

SolarisManagerResult solaris_manager_icloud_delete(const char *name, double timeout_seconds,
                                                   char **error) {
  last_timeout = timeout_seconds;
  if (strcmp(name, saved_name) == 0) saved_name[0] = 0;
  return finish(error);
}

void solaris_manager_free(void *pointer) {
  if (pointer == NULL) return;
  outstanding--;
  free(pointer);
}
"""


@pytest.fixture
def fake_library(tmp_path: Path) -> Path:
    if sys.platform != "darwin" or shutil.which("xcrun") is None:
        pytest.skip("builds a stand-in dylib with Apple's clang")
    source = tmp_path / "fake.c"
    source.write_text(FAKE_MANAGER_C)
    library = tmp_path / "SolarisManager.dylib"
    command = ["xcrun", "clang", "-dynamiclib", "-I", str(HEADER.parent), str(source)]
    subprocess.run([*command, "-o", str(library)], check=True)
    return library


def test_manager_library_calls_and_releases_every_buffer(fake_library: Path) -> None:
    manager = ManagerLibrary(fake_library)
    fake = ctypes.CDLL(str(fake_library))
    fake.fake_last_timeout.restype = ctypes.c_double

    assert manager.send(b"\x00\x01\xff\x00", 2.5) == b"\x00\xff\x01\x00"
    assert fake.fake_last_timeout() == 2.5
    assert manager.send(b"", 1.0) == b""
    assert manager.status() == extension.TUNNEL_REASSERTING
    manager.configure(True)
    assert fake.fake_last_include_all_networks() == 1
    manager.activate(60.0)
    manager.deactivate(60.0)
    assert fake.fake_last_timeout() == 60.0
    manager.start(30.0, False)
    assert (fake.fake_last_timeout(), fake.fake_last_include_all_networks()) == (30.0, 0)
    manager.start(30.0, True)
    assert fake.fake_last_include_all_networks() == 1
    manager.stop(15.0)
    assert fake.fake_last_timeout() == 15.0
    for call in (lambda: manager.send(b"x", 1.0), lambda: manager.start(1.0, False)):
        fake.fake_fail_next(extension.NOT_CONNECTED)
        with pytest.raises(ManagerError) as raised:
            call()
        assert (raised.value.result, raised.value.message) == (
            extension.NOT_CONNECTED,
            "fake failure 4",
        )
    assert fake.fake_outstanding() == 0


def test_manager_library_calls_icloud_and_releases_every_buffer(fake_library: Path) -> None:
    manager = ManagerLibrary(fake_library)
    fake = ctypes.CDLL(str(fake_library))
    fake.fake_last_timeout.restype = ctypes.c_double
    fake.fake_saved_info.restype = ctypes.c_char_p

    assert manager.icloud_status(15.0) == backup_icloud.NO_ACCOUNT
    assert fake.fake_last_timeout() == 15.0
    manager.icloud_save("solaris-backup-a", '{"device": "笔记本"}', b"\x00snap\xff", 120.0)
    assert (fake.fake_last_timeout(), fake.fake_saved_info().decode()) == (
        120.0,
        '{"device": "笔记本"}',
    )
    assert manager.icloud_list(60.0) == '[{"name":"solaris-backup-a","info":"6 bytes"}]'
    assert manager.icloud_download("solaris-backup-a", 30.0) == b"\x00snap\xff"
    manager.icloud_delete("solaris-backup-a", 30.0)
    for call in (
        lambda: manager.icloud_download("solaris-backup-a", 1.0),
        lambda: manager.icloud_download("solaris-backup-b", 1.0),
    ):
        with pytest.raises(ManagerError) as raised:
            call()
        assert raised.value.result == extension.NOT_FOUND
    for call in (lambda: manager.icloud_list(1.0), lambda: manager.icloud_save("x", "", b"", 1.0)):
        fake.fake_fail_next(extension.UNREACHABLE)
        with pytest.raises(ManagerError) as raised:
            call()
        assert (raised.value.result, raised.value.message) == (
            extension.UNREACHABLE,
            "fake failure 10",
        )
    assert fake.fake_outstanding() == 0


def test_bundled_provider_needs_the_manager_in_the_bundle(
    fake_library: Path, tmp_path: Path
) -> None:
    executable = tmp_path / "Solaris.app" / "Contents" / "MacOS" / "SolarisUI"
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert backup_icloud.bundled_provider(executable) is None
    assert backup_icloud.bundled_provider() is None  # from source

    frameworks = executable.parent.parent / "Frameworks"
    frameworks.mkdir()
    shutil.copy(fake_library, frameworks / extension.LIBRARY_NAME)
    provider = backup_icloud.bundled_provider(executable)
    assert provider is not None
    assert asyncio.run(provider.status()) == backup_icloud.NO_ACCOUNT


def test_bundled_host_needs_the_manager_in_the_bundle(fake_library: Path, tmp_path: Path) -> None:
    contents = tmp_path / "Solaris.app" / "Contents"
    executable = contents / "MacOS" / "Solaris"
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert bundled_host(executable) is None

    (contents / "Frameworks").mkdir()
    shutil.copy(fake_library, contents / "Frameworks" / extension.LIBRARY_NAME)
    info = {"CFBundleIdentifier": "dev.tschen.solaris", extension.CORE_VERSION_KEY: "v1.19.33"}
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    host = bundled_host(executable)
    assert host is not None
    # The stand-in reports a reasserting tunnel that does not answer.
    fake = ctypes.CDLL(str(contents / "Frameworks" / extension.LIBRARY_NAME))
    fake.fake_fail_next(extension.NOT_CONNECTED)
    info = asyncio.run(host.get_info())
    assert (info.core_version, info.state) == ("v1.19.33", "starting")
