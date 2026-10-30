"""The latest release on GitHub, and which of its assets this machine installs
(docs/release.md §1, §2).

Every call blocks on the network: run it on a worker thread.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import platform
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import IO, Any
from urllib.parse import urlsplit

from solaris import __version__
from solaris.product import DISPLAY_NAME, PRODUCT_ID

REPOSITORY = "Ember-Moth/Solaris"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
USER_AGENT = f"{PRODUCT_ID}/{__version__}"
CHECKSUMS_NAME = "SHA256SUMS"
TIMEOUT_S = 30.0
MAX_RELEASE_BYTES = 1024 * 1024  # the release's JSON
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

type Version = tuple[int, int, int]

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class UpdateError(Exception):
    """``code`` names the step that failed (docs/release.md §3); ``message`` is the
    technical detail, in English."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def parse_version(text: str) -> Version | None:
    """``0.2.0`` or a tag ``v0.2.0``; None for anything else, pre-releases included."""
    match = _VERSION.fullmatch(text.strip().removeprefix("v"))
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def is_newer(version: str, than: str) -> bool:
    """Whether ``version`` comes after ``than``. A version that does not parse is never
    newer; any release is newer than a current version that does not parse."""
    new = parse_version(version)
    if new is None:
        return False
    current = parse_version(than)
    return current is None or new > current


def installer_name(
    version: str, system: str = sys.platform, machine: str = platform.machine()
) -> str | None:
    """The asset this machine installs, or None where no installer is built (D2)."""
    machine = machine.lower()
    if system == "darwin" and machine == "arm64":
        return f"{DISPLAY_NAME}-{version}-macos-arm64.dmg"
    if system == "win32" and machine in ("amd64", "x86_64"):
        return f"{DISPLAY_NAME}-{version}-windows-x64-setup.exe"
    return None


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    url: str  # browser_download_url


@dataclass(frozen=True)
class Release:
    version: str  # the tag without its ``v``
    page_url: str  # the release on github.com, for its notes
    assets: tuple[Asset, ...]

    def asset(self, name: str) -> Asset | None:
        return next((asset for asset in self.assets if asset.name == name), None)


def check_download_url(url: str) -> str:
    """https only; plain http only to a loopback address, for the tests' fake server."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname and _loopback(parts.hostname):
        return url
    raise UpdateError("bad_release", f"not an https link: {url}")


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def open_url(
    url: str, *, timeout_s: float = TIMEOUT_S, headers: Mapping[str, str] | None = None
) -> IO[bytes]:
    """A response to read; network failures and HTTP errors raise ``check_failed``."""
    request = urllib.request.Request(
        check_download_url(url), headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout_s)
    except urllib.error.HTTPError as error:
        raise UpdateError("check_failed", f"HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise UpdateError("check_failed", str(error.reason)) from error
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise UpdateError("check_failed", str(error) or type(error).__name__) from error


def read_limited(response: IO[bytes], limit: int) -> bytes:
    try:
        with response:
            body = response.read(limit + 1)
    except (OSError, http.client.HTTPException) as error:
        raise UpdateError("check_failed", str(error) or type(error).__name__) from error
    if len(body) > limit:
        raise UpdateError("bad_release", f"more than {limit} bytes")
    return body


def parse_release(data: Mapping[str, Any]) -> Release:
    tag = data.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None:
        raise UpdateError("bad_release", f"tag {tag!r}")
    assets = []
    for item in data.get("assets") or ():
        if not isinstance(item, Mapping):
            continue
        name, size, url = item.get("name"), item.get("size"), item.get("browser_download_url")
        if isinstance(name, str) and isinstance(size, int) and isinstance(url, str):
            assets.append(Asset(name, size, url))
    page = data.get("html_url")
    return Release(
        version=tag.strip().removeprefix("v"),
        page_url=page if isinstance(page, str) else "",
        assets=tuple(assets),
    )


class ReleaseSource:
    """GitHub's latest release of ``REPOSITORY``: neither drafts nor pre-releases. No
    authentication; GitHub allows 60 such requests an hour from one address."""

    def __init__(self, url: str = LATEST_URL, *, timeout_s: float = TIMEOUT_S) -> None:
        self._url = url
        self._timeout_s = timeout_s

    def latest(self) -> Release:
        response = open_url(self._url, timeout_s=self._timeout_s, headers=GITHUB_HEADERS)
        body = read_limited(response, MAX_RELEASE_BYTES)
        try:
            data = json.loads(body)
        except ValueError as error:
            raise UpdateError("bad_release", str(error)) from error
        if not isinstance(data, Mapping):
            raise UpdateError("bad_release", "not a JSON object")
        return parse_release(data)
