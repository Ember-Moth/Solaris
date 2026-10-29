"""Download a subscription and read a local config into the text the service validates.

Downloads use urllib on a worker thread so the asyncio loop stays responsive; urllib
follows the system proxy settings, as other subscription clients do.
"""

from __future__ import annotations

import asyncio
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from mihomo_desktop import __version__
from mihomo_desktop.models.profiles import ProfileError, Usage
from mihomo_desktop.product import PRODUCT_ID
from mihomo_desktop.protocol.frame import MAX_REQUEST_BYTES

# Providers serve Clash YAML to user agents that name a Clash core.
USER_AGENT = f"{PRODUCT_ID}/{__version__} clash.meta"
TIMEOUT_S = 30.0
# Start and Reload carry the whole config in one request frame.
MAX_CONFIG_BYTES = MAX_REQUEST_BYTES - 1024 * 1024
_USAGE_KEYS = ("upload", "download", "total", "expire")
_CONFIG_SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True)
class Download:
    body: bytes
    usage: Usage | None
    filename: str | None  # suggested by Content-Disposition


class Fetcher(Protocol):
    async def fetch(self, url: str) -> Download: ...


def parse_userinfo(header: str | None) -> Usage | None:
    """``upload=455727941; download=6174315083; total=1073741824000; expire=1671815872``.

    Missing or malformed fields count as 0; None when the header names no field at all."""
    if not header:
        return None
    values: dict[str, int] = {}
    for part in header.split(";"):
        key, separator, value = part.partition("=")
        key = key.strip().lower()
        if not separator or key not in _USAGE_KEYS:
            continue
        try:
            values[key] = max(int(float(value.strip())), 0)
        except ValueError, OverflowError:
            values[key] = 0
    if not values:
        return None
    return Usage(*(values.get(key, 0) for key in _USAGE_KEYS))


def suggested_name(content_disposition: str | None) -> str | None:
    """The profile name a provider suggests through its download's file name."""
    if not content_disposition:
        return None
    message = Message()
    message["content-disposition"] = content_disposition
    filename = message.get_filename()
    if not filename:
        return None
    name = Path(filename.replace("\\", "/")).name
    for suffix in _CONFIG_SUFFIXES:
        name = name.removesuffix(suffix)
    return name.strip() or None


def decode_config(body: bytes) -> str:
    if len(body) > MAX_CONFIG_BYTES:
        raise ProfileError("too_large", f"{len(body)} bytes")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ProfileError("not_text", str(error)) from error
    if not text.strip():
        raise ProfileError("empty_config", "the config is empty")
    return text


def read_config_file(path: Path) -> str:
    try:
        with path.open("rb") as file:
            body = file.read(MAX_CONFIG_BYTES + 1)
    except OSError as error:
        raise ProfileError("file_unreadable", error.strerror or str(error)) from error
    return decode_config(body)


def check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProfileError("invalid_url", url)
    return url


class UrlFetcher:
    def __init__(self, *, timeout_s: float = TIMEOUT_S, max_bytes: int = MAX_CONFIG_BYTES) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> Download:
        return await asyncio.to_thread(self._fetch, check_url(url))

    def _fetch(self, url: str) -> Download:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = response.read(self._max_bytes + 1)
                headers = response.headers
        except urllib.error.HTTPError as error:
            raise ProfileError("http_error", f"HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ProfileError("download_failed", str(error.reason)) from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise ProfileError("download_failed", str(error) or type(error).__name__) from error
        if len(body) > self._max_bytes:
            raise ProfileError("too_large", f"more than {self._max_bytes} bytes")
        return Download(
            body=body,
            usage=parse_userinfo(headers.get("Subscription-Userinfo")),
            filename=suggested_name(headers.get("Content-Disposition")),
        )
