import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from solaris.models.profiles import ProfileError, Usage, count_proxies
from solaris.models.subscription import (
    USER_AGENT,
    UrlFetcher,
    decode_config,
    parse_userinfo,
    read_config_file,
    suggested_name,
)
from tests.support.simulated import NODES, SAMPLE_CONFIG


@pytest.mark.parametrize(
    ("header", "usage"),
    [
        (
            "upload=455727941; download=6174315083; total=1073741824000; expire=1671815872",
            Usage(455727941, 6174315083, 1073741824000, 1671815872),
        ),
        ("upload=1;download=2;total=3", Usage(1, 2, 3, 0)),
        ("Upload = 1.5e3; expire=", Usage(1500, 0, 0, 0)),
        ("download=-5; total=nan; color=blue", Usage(0, 0, 0, 0)),
        ("color=blue", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_userinfo(header: str | None, usage: Usage | None) -> None:
    assert parse_userinfo(header) == usage


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ('attachment; filename="My Sub.yaml"', "My Sub"),
        ("attachment; filename*=UTF-8''%E7%A4%BA%E4%BE%8B%E8%AE%A2%E9%98%85", "示例订阅"),
        ('attachment; filename="../../etc/x.yml"', "x"),
        ("inline", None),
        (None, None),
    ],
)
def test_suggested_name(header: str | None, name: str | None) -> None:
    assert suggested_name(header) == name


def test_decode_config() -> None:
    assert decode_config("﻿proxies: []".encode()) == "proxies: []"
    for body, code in ((b"\xff\xfe", "not_text"), (b" \n", "empty_config")):
        with pytest.raises(ProfileError) as raised:
            decode_config(body)
        assert raised.value.code == code


def test_read_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes(SAMPLE_CONFIG.encode())  # write_text would turn \n into \r\n on Windows
    assert read_config_file(path) == SAMPLE_CONFIG
    with pytest.raises(ProfileError) as raised:
        read_config_file(tmp_path / "missing.yaml")
    assert raised.value.code == "file_unreadable"


@pytest.mark.parametrize(
    ("text", "count"),
    [
        (SAMPLE_CONFIG, len(NODES)),
        ("proxies:\n- name: a\n  type: ss\n- name: b\n  type: ss\nrules: []\n", 2),
        ("proxies:\n  # a comment\n  - name: a\n\n    type: ss\nproxy-groups:\n  - name: g\n", 1),
        ("proxies: []\n", 0),
        ("proxies: [{name: a}]\n", None),
        ("proxy-providers:\n  p: {type: http}\n", None),
        ("proxies:\nrules: []\n", None),
    ],
)
def test_count_proxies(text: str, count: int | None) -> None:
    assert count_proxies(text) == count


class _Handler(BaseHTTPRequestHandler):
    agents: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.agents.append(self.headers.get("User-Agent", ""))
        if self.path == "/missing":
            self.send_error(404)
            return
        body = SAMPLE_CONFIG.encode() if self.path != "/big" else b"x" * 4096
        self.send_response(200)
        self.send_header("Content-Type", "text/yaml; charset=utf-8")
        self.send_header("Subscription-Userinfo", "upload=1; download=2; total=10; expire=99")
        self.send_header("Content-Disposition", 'attachment; filename="Local Sub.yaml"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Reach the loopback server directly even if the machine has a proxy configured.
    monkeypatch.setenv("no_proxy", "*")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_url_fetcher_downloads_with_usage(server: str) -> None:
    download = asyncio.run(UrlFetcher().fetch(server + "/sub"))
    assert download.body.decode() == SAMPLE_CONFIG
    assert download.usage == Usage(1, 2, 10, 99)
    assert download.filename == "Local Sub"
    assert _Handler.agents[-1] == USER_AGENT


@pytest.mark.parametrize(
    ("path", "code"),
    [("/missing", "http_error"), ("/big", "too_large")],
)
def test_url_fetcher_failures(server: str, path: str, code: str) -> None:
    with pytest.raises(ProfileError) as raised:
        asyncio.run(UrlFetcher(max_bytes=1024).fetch(server + path))
    assert raised.value.code == code


def test_url_fetcher_rejects_other_schemes_and_dead_hosts(server: str) -> None:
    fetcher = UrlFetcher(timeout_s=2)
    for url in ("file:///etc/passwd", "sub.example.com", "https://"):
        with pytest.raises(ProfileError) as raised:
            asyncio.run(fetcher.fetch(url))
        assert raised.value.code == "invalid_url"
    closed = server.rsplit(":", 1)[0] + ":1"
    with pytest.raises(ProfileError) as raised:
        asyncio.run(fetcher.fetch(closed))
    assert raised.value.code == "download_failed"
