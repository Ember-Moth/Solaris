"""The window under test on the development service, the local network it reaches through
the mixed port, and the profiles the tests import."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from mihomo_desktop.viewmodels.composition import ViewModels
from scripts.dev import DevService
from tests.e2e.servers import ProxyNode, WebServer

WAIT_MS = 15_000
# A directory to save screenshots of the window in, as the tests reach each page.
SCREENSHOTS_ENV = "MIHOMO_DESKTOP_E2E_SCREENSHOTS"
WINDOW_SIZE = (1280, 800)


@dataclass(frozen=True)
class Network:
    proxied: WebServer  # the profile's rules send it through 节点选择
    direct: WebServer  # left to MATCH,DIRECT
    node_a: ProxyNode
    node_b: ProxyNode


def nodes_profile(network: Network) -> str:
    """Two nodes in a selector. It also asks for what the development service must refuse
    or clear: a TUN, LAN access, a redirect port and an external controller."""
    return f"""\
mixed-port: 7890
allow-lan: true
bind-address: "*"
redir-port: 7892
external-controller: 127.0.0.1:19090
external-controller-unix: controller.sock
secret: e2e
tun:
  enable: true
  stack: system
  auto-route: true
  auto-detect-interface: true
mode: rule
log-level: info
proxies:
  - {{name: node-a, type: http, server: 127.0.0.1, port: {network.node_a.port}}}
  - {{name: node-b, type: http, server: 127.0.0.1, port: {network.node_b.port}}}
proxy-groups:
  - name: 节点选择
    type: select
    proxies: [node-a, node-b, DIRECT]
    url: {network.proxied.url("/generate_204")}
rules:
  - DST-PORT,{network.proxied.port},节点选择
  - MATCH,DIRECT
"""


# Only DIRECT: no proxies, no groups. The same requests as nodes_profile.
DIRECT_PROFILE = """\
mixed-port: 7890
allow-lan: true
external-controller: 127.0.0.1:19090
tun:
  enable: true
rules:
  - MATCH,DIRECT
"""


class Stream:
    """A slow download through the mixed port, read on a thread of its own."""

    def __init__(self, mixed_port: int, url: str) -> None:
        self.received = 0
        self.error = ""
        self.done = threading.Event()
        self._socket = socket.create_connection(("127.0.0.1", mixed_port), timeout=10)
        authority = url.split("/")[2]
        request = f"GET {url} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n"
        self._socket.sendall(request.encode())
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while data := self._socket.recv(65536):
                self.received += len(data)
        except OSError as error:
            self.error = str(error)
        finally:
            self.done.set()
            self._socket.close()


class App:
    def __init__(
        self,
        qtbot: QtBot,
        view_models: ViewModels,
        window: QQuickWindow,
        warnings: list[str],
        service: DevService,
        network: Network,
        data: Path,
    ) -> None:
        self.qtbot = qtbot
        self.view_models = view_models
        self.window = window
        self.warnings = warnings
        self.service = service
        self.network = network
        self.data = data

    # -- waiting and reading -----------------------------------------------------------------
    def until(self, test: Callable[[], object], timeout: int = WAIT_MS) -> None:
        self.qtbot.waitUntil(lambda: bool(test()), timeout=timeout)

    @staticmethod
    def prop(view_model: QObject, name: str) -> Any:
        return view_model.property(name)

    def rows(self, view_model: QObject) -> list[Any]:
        return self.prop(view_model, "model").rows

    def rows_of(self, view_model: QObject, name: str) -> list[Any]:
        """The rows of another list model property, such as the routing page's rules."""
        return list(self.prop(view_model, name).rows)

    @property
    def mixed_port(self) -> int:
        return self.view_models.settings_store.current.mixed_port

    # -- profiles ----------------------------------------------------------------------------
    def write(self, name: str, text: str) -> Path:
        path = self.data.parent / name
        path.write_text(text, encoding="utf-8")
        return path

    def import_file(self, path: Path) -> None:
        profiles = self.view_models.profiles
        count = len(self.rows(profiles))
        profiles.importFile(QUrl.fromLocalFile(str(path)).toString())
        self.until(lambda: not self.prop(profiles, "importing"))
        assert not self.prop(profiles, "hasError"), self.prop(profiles, "summary")
        assert len(self.rows(profiles)) == count + 1

    def import_url(self, url: str, name: str = "") -> None:
        profiles = self.view_models.profiles
        count = len(self.rows(profiles))
        profiles.importUrl(url, name, True)
        self.until(lambda: not self.prop(profiles, "importing"))
        assert self.prop(profiles, "importError") == ""
        assert len(self.rows(profiles)) == count + 1

    def profile_id(self, name: str) -> str:
        return next(r.id for r in self.rows(self.view_models.profiles) if r.name == name)

    # -- tunnel ------------------------------------------------------------------------------
    def connect(self) -> None:
        overview = self.view_models.overview
        self.until(lambda: self.prop(overview, "statusTitle") in ("未连接", "连接失败"))
        overview.toggleConnection()
        self.until(lambda: self.prop(overview, "connected") or self.prop(overview, "switchError"))
        assert self.prop(overview, "connected"), self.prop(overview, "switchError")

    def curl(self, url: str) -> str:
        """The body fetched through the mixed port, or curl's error."""
        done = subprocess.run(
            ["curl", "-sS", "--max-time", "5", "-x", f"http://127.0.0.1:{self.mixed_port}", url],
            capture_output=True,
            text=True,
            check=False,
        )
        return (done.stdout or done.stderr).strip()

    def screenshot(self, name: str) -> None:
        directory = os.environ.get(SCREENSHOTS_ENV)
        if not directory:
            return
        self.window.resize(*WINDOW_SIZE)
        self.qtbot.wait(300)  # page transitions and the next poll
        path = Path(directory) / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        assert self.window.grabWindow().save(str(path))

    def stream(self, seconds: float) -> Stream:
        return Stream(self.mixed_port, self.network.proxied.url(f"/slow?seconds={seconds}"))

    def stream_url(self, url: str) -> Stream:
        return Stream(self.mixed_port, url)
