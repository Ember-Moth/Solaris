"""A fake GitHub on a loopback port: the latest release's JSON and its assets
(docs/release.md §8). No Release exists yet, so every update test talks to this."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from solaris.update.releases import CHECKSUMS_NAME, Asset

LATEST_PATH = "/repos/Ember-Moth/Solaris/releases/latest"


@dataclass
class FakeGitHub:
    base: str  # http://127.0.0.1:<port>
    tag: str = "v0.2.0"
    files: dict[str, bytes] = field(default_factory=dict)  # asset name to body
    declared: dict[str, int] = field(default_factory=dict)  # a size other than the body's
    status: int = 200  # of the release's JSON
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    @property
    def latest_url(self) -> str:
        return self.base + LATEST_PATH

    def add(self, name: str, body: bytes) -> None:
        self.files[name] = body

    def add_checksums(self, *names: str, wrong: str = "") -> None:
        lines = []
        for name in names:
            digest = hashlib.sha256(self.files[name]).hexdigest()
            if name == wrong:
                digest = "0" * 64
            lines.append(f"{digest}  {name}\n")
        self.files[CHECKSUMS_NAME] = "".join(lines).encode()

    def asset(self, name: str) -> Asset:
        size = self.declared.get(name, len(self.files[name]))
        return Asset(name, size, f"{self.base}/download/{name}")

    def release_json(self) -> dict[str, object]:
        return {
            "tag_name": self.tag,
            "html_url": f"https://github.com/Ember-Moth/Solaris/releases/tag/{self.tag}",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": name,
                    "size": self.declared.get(name, len(body)),
                    "browser_download_url": f"{self.base}/download/{name}",
                }
                for name, body in self.files.items()
            ],
        }


def _handler(github: FakeGitHub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            github.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            if self.path == LATEST_PATH:
                if github.status != 200:
                    self.send_error(github.status)
                    return
                self._send(json.dumps(github.release_json()).encode())
            elif self.path.startswith("/download/"):
                name = self.path.removeprefix("/download/")
                body = github.files.get(name)
                if body is None:
                    self.send_error(404)
                    return
                self._send(body)
            else:
                self.send_error(404)

        def _send(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@contextmanager
def fake_github() -> Iterator[FakeGitHub]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    github = FakeGitHub(f"http://127.0.0.1:{server.server_address[1]}")
    server.RequestHandlerClass = _handler(github)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield github
    finally:
        server.shutdown()
        server.server_close()


class FakeVerifier:
    """Accepts what it is told to; records what it saw."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: list[Path] = []

    def verify(self, path: Path) -> None:
        self.seen.append(path)
        if self.error is not None:
            raise self.error
