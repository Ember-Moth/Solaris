"""Local stand-ins for the internet, all on 127.0.0.1: a web server that also serves a
subscription and a slow download, and HTTP proxies that play the nodes of a profile.

Each records what reached it, so a test can tell which way a request went.
"""

from __future__ import annotations

import http.server
import random
import select
import socket
import socketserver
import threading
import time
from urllib.parse import parse_qs, urlsplit

SLOW_CHUNK = b"x" * 1024
SLOW_INTERVAL_S = 0.1


class _ThreadingServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False


class WebServer:
    """``/`` answers with the server's name; ``/generate_204`` serves health checks;
    ``/slow?seconds=N`` sends a chunk every 0.1 s for N seconds; ``/sub`` serves
    ``subscription`` with a ``Subscription-Userinfo`` header."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.paths: list[str] = []
        self.subscription = b""
        self.userinfo = ""
        self.filename = ""
        self.slow_sent = 0  # bytes of the slow download written so far
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                server.paths.append(parts.path)
                if parts.path == "/generate_204":
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif parts.path == "/slow":
                    seconds = float(parse_qs(parts.query).get("seconds", ["30"])[0])
                    self._slow(seconds)
                elif parts.path == "/sub":
                    headers = {"Subscription-Userinfo": server.userinfo}
                    if server.filename:
                        headers["Content-Disposition"] = f'attachment; filename="{server.filename}"'
                    self._send(server.subscription, headers)
                else:
                    self._send(f"{server.name}\n".encode(), {})

            def _send(self, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _slow(self, seconds: float) -> None:
                count = int(seconds / SLOW_INTERVAL_S)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(count * len(SLOW_CHUNK)))
                self.end_headers()
                for _ in range(count):
                    self.wfile.write(SLOW_CHUNK)
                    self.wfile.flush()
                    server.slow_sent += len(SLOW_CHUNK)
                    time.sleep(SLOW_INTERVAL_S)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = _ThreadingServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> WebServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


class ProxyNode:
    """An HTTP proxy that only tunnels (CONNECT), as mihomo's ``http`` outbound uses it.
    ``targets`` lists the ``host:port`` of every tunnel it opened."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.targets: list[str] = []
        node = self

        class Handler(socketserver.StreamRequestHandler):
            rbufsize = 0  # read no further than the request, so the tunnel loses nothing

            def handle(self) -> None:
                request = self.rfile.readline().decode("latin-1").split()
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                if len(request) < 2 or request[0] != "CONNECT":
                    self.wfile.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                    return
                host, _, port = request[1].rpartition(":")
                try:
                    upstream = socket.create_connection((host.strip("[]"), int(port)), timeout=5)
                except OSError:
                    self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                node.targets.append(request[1])
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.wfile.flush()
                with upstream:
                    _pipe(self.connection, upstream)

        self._server = _ThreadingServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> ProxyNode:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def _pipe(client: socket.socket, upstream: socket.socket) -> None:
    """Copy both ways until either side closes."""
    client.settimeout(None)
    upstream.settimeout(None)
    sockets = [client, upstream]
    while True:
        readable, _, _ = select.select(sockets, [], [], 1.0)
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


def listening_port() -> tuple[socket.socket, int]:
    """A socket that holds a port on 127.0.0.1, as another program would."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    return holder, holder.getsockname()[1]


def free_port() -> int:
    """A port on 127.0.0.1 free for both TCP and UDP, as the mixed port needs. It lies below
    the systems' ephemeral ranges (49152 up on macOS, 32768 up on Linux), where every
    outgoing socket takes its port and can take this one before the core binds it."""
    for port in random.sample(range(20000, 32768), 100):
        with (
            socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp,
        ):
            try:
                tcp.bind(("127.0.0.1", port))
                udp.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port on 127.0.0.1")
