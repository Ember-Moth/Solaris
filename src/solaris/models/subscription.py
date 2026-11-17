"""Download a subscription and read a local config into the text the service validates.

Downloads use urllib on a worker thread so the asyncio loop stays responsive; urllib
follows the system proxy settings, as other subscription clients do.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import http.client
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from solaris import __version__
from solaris.models.profiles import ProfileError, Usage
from solaris.product import PRODUCT_ID
from solaris.protocol.frame import MAX_REQUEST_BYTES

# Providers serve Clash YAML to user agents that name a Clash core.
USER_AGENT = f"{PRODUCT_ID}/{__version__} clash.meta"
TIMEOUT_S = 30.0
# Start and Reload carry the whole config in one request frame.
MAX_CONFIG_BYTES = MAX_REQUEST_BYTES - 1024 * 1024
_USAGE_KEYS = ("upload", "download", "total", "expire")
_USERINFO_HEADERS = (
    "Subscription-Userinfo",
    "X-Amz-Meta-Subscription-Userinfo",
    "X-Obs-Meta-Subscription-Userinfo",
    "X-Cos-Meta-Subscription-Userinfo",
)
_CONFIG_SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True)
class EntityTag:
    value: str
    resource: str  # digest of the final HTTP URL; signed redirect URLs stay out of the DB

    def matches(self, url: str) -> bool:
        return self.resource == resource_id(url)


def resource_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


class _ConditionalRequest(urllib.request.BaseHandler):
    def __init__(self, tag: EntityTag) -> None:
        self.tag = tag

    def http_request(self, request: urllib.request.Request) -> urllib.request.Request:
        if self.tag.matches(request.full_url):
            # urllib must re-evaluate the resource on every redirect.
            request.add_unredirected_header("If-None-Match", self.tag.value)
        return request

    https_request = http_request


@dataclass(frozen=True)
class Download:
    body: bytes
    usage: Usage | None
    filename: str | None  # suggested by Content-Disposition
    interval: int | None = None
    title: str = ""
    website: str = ""
    support: str = ""
    etag: EntityTag | None = None


@dataclass(frozen=True)
class NotModified:
    usage: Usage | None
    website: str | None = None
    support: str | None = None


class Fetcher(Protocol):
    async def fetch(self, url: str) -> Download: ...

    async def fetch_if_changed(self, url: str, etag: EntityTag) -> Download | NotModified: ...


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


def usage_from_headers(headers: Message) -> Usage | None:
    for name in _USERINFO_HEADERS:
        if (value := headers.get(name)) is not None:
            return parse_userinfo(value)
    return None


def web_link(value: str) -> str:
    value = value.strip()
    if (
        not value
        or "\\" in value
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return ""
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and parts.username is None:
            _ = parts.port  # invalid or out-of-range port
            return value
    except ValueError:
        pass
    return ""


def _title(value: str) -> str:
    value = value.strip()
    if value.startswith("base64:"):
        try:
            value = base64.b64decode(value[7:], validate=True).decode("utf-8")
        except ValueError:
            return ""
    elif not value.isascii():
        # HTTPMessage decodes wire bytes as Latin-1. Prefer a UTF-8 title when
        # the bytes are UTF-8; an ordinary Latin-1 title is already decoded.
        with contextlib.suppress(UnicodeError):
            value = value.encode("latin-1").decode("utf-8")
    return "".join(c for c in value if unicodedata.category(c)[0] != "C").strip()[:100]


def _etag(value: str, url: str) -> EntityTag | None:
    # A single RFC 9110 entity tag, including weak tags. A wildcard cannot identify a body.
    return (
        EntityTag(value, resource_id(url))
        if re.fullmatch(r'(?:W/)?"[!#-~\x80-\xff]*"', value)
        else None
    )


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


def node_links(text: str) -> str:
    """Keep one share URI per line; the core owns protocol parsing and validation.

    Local node profiles use the existing file-profile storage/backup format, with
    no external path to re-read. Do not download these URIs as subscriptions.
    """
    text = decode_config(text.encode("utf-8"))
    lines = list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))
    for number, line in enumerate(lines, 1):
        scheme, separator, body = line.partition("://")
        if (
            not separator
            or not body
            or not scheme.isascii()
            or not scheme.replace("+", "").isalnum()
        ):
            raise ProfileError("invalid_node_links", f"line {number}")
    return "\n".join(lines) + "\n"


class UrlFetcher:
    def __init__(self, *, timeout_s: float = TIMEOUT_S, max_bytes: int = MAX_CONFIG_BYTES) -> None:
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> Download:
        response = await asyncio.to_thread(self._fetch, check_url(url), None)
        assert isinstance(response, Download)
        return response

    async def fetch_if_changed(self, url: str, etag: EntityTag) -> Download | NotModified:
        return await asyncio.to_thread(self._fetch, check_url(url), etag)

    def _fetch(self, url: str, etag: EntityTag | None) -> Download | NotModified:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = (
            urllib.request.build_opener(_ConditionalRequest(etag))
            if etag
            else urllib.request.build_opener()
        )
        try:
            with opener.open(request, timeout=self._timeout_s) as response:
                if response.status == 206:
                    raise ProfileError("download_failed", "partial subscription response")
                length = getattr(response, "length", None)
                body = response.read(self._max_bytes + 1)
                if length is not None and len(body) < min(length, self._max_bytes + 1):
                    raise ProfileError("download_failed", "incomplete subscription response")
                headers = response.headers
                resource_url = response.geturl()
        except urllib.error.HTTPError as error:
            with error:
                if error.code == 304 and etag and etag.matches(error.geturl()):
                    headers = error.headers
                    return NotModified(
                        usage_from_headers(headers),
                        web_link(headers["Profile-Web-Page-Url"])
                        if "Profile-Web-Page-Url" in headers
                        else None,
                        web_link(headers["Support-Url"]) if "Support-Url" in headers else None,
                    )
                raise ProfileError("http_error", f"HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ProfileError("download_failed", str(error.reason)) from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise ProfileError("download_failed", str(error) or type(error).__name__) from error
        if len(body) > self._max_bytes:
            raise ProfileError("too_large", f"more than {self._max_bytes} bytes")
        hours = headers.get("Profile-Update-Interval", "").strip()
        interval = int(hours) if re.fullmatch(r"[0-9]{1,10}", hours) else 0
        return Download(
            body=body,
            usage=usage_from_headers(headers),
            filename=suggested_name(headers.get("Content-Disposition")),
            title=_title(headers.get("Profile-Title", "")),
            interval=interval if 0 < interval <= 2_147_483_647 else None,
            website=web_link(headers.get("Profile-Web-Page-Url", "")),
            support=web_link(headers.get("Support-Url", "")),
            etag=_etag(headers.get("ETag", ""), resource_url),
        )
