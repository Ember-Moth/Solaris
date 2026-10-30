"""Google Drive backup (docs/storage.md §4): snapshots in the hidden appDataFolder of the
user's Drive, through the Drive v3 REST API over urllib, in the user's own context and
through the system proxy, as subscription downloads go.

Signing in follows OAuth 2.0 for installed apps: Google's consent page opens in the system
browser and redirects to a listener on a random loopback port of 127.0.0.1, with PKCE. The
one scope is drive.appdata, so the app sees its own files and no other. The refresh token
lives in the system's credential store (credentials.py, D14), never in the database, a
backup or diagnostics; access tokens stay in memory. Signing out revokes the grant and
deletes the token.

The OAuth client is the project's "Desktop app" client. Its ID and secret are not in the
source: from source SOLARIS_GOOGLE_CLIENT_ID and SOLARIS_GOOGLE_CLIENT_SECRET give them, and
a build bundles them as CLIENT_FILE (scripts/build_apple.py). Google does not treat an
installed app's secret as confidential. Without a client there is no provider.

Each snapshot is one file named as the directory provider names its files; its
SnapshotInfo travels in the file's appProperties, so that listing downloads no snapshot.
An entry's id is the file's Drive id.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import http.client
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from solaris import __version__
from solaris.host.extension import _blocking  # a request in flight must not hold up the exit
from solaris.models.backup import MAX_SNAPSHOT_BYTES, BackupError, Counts, Snapshot, SnapshotInfo
from solaris.models.backup_providers import BackupEntry
from solaris.models.credentials import CredentialError, CredentialStore
from solaris.product import PRODUCT_ID

GOOGLE_DRIVE = "google_drive"
DISPLAY_NAME = "Google Drive"
SCOPE = "https://www.googleapis.com/auth/drive.appdata"
CLIENT_ID_ENV = "SOLARIS_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "SOLARIS_GOOGLE_CLIENT_SECRET"
CLIENT_FILE_NAME = "google_client.json"
CLIENT_FILE = Path(__file__).resolve().parent.parent / CLIENT_FILE_NAME  # beside the package
TOKEN_KEY = "google-drive"  # in the credential store

SIGN_IN_TIMEOUT_S = 600.0
REQUEST_TIMEOUT_S = 60.0
# A browser may open a connection and send nothing on it.
REDIRECT_READ_TIMEOUT_S = 10.0
# An access token is refreshed this long before Google says it expires.
EXPIRY_MARGIN_S = 60.0
USER_AGENT = f"{PRODUCT_ID}/{__version__}"

_NAME = re.compile(r"solaris-backup-\d{8}-\d{6}-[0-9a-f]{12}\.json")
_FILE_ID = re.compile(r"[A-Za-z0-9_-]+")
_JSON_BYTES = 4 * 1024 * 1024
_ERROR_BYTES = 64 * 1024
# Drive keeps at most 124 bytes of key and value together for each app property.
_PROPERTY_BYTES = 124
# Reasons of a Drive error (error.errors[].reason) that mean the user's storage is full,
# and those worth trying again later.
_FULL = {"storageQuotaExceeded"}
_LATER = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoogleClient:
    id: str
    secret: str


@dataclass(frozen=True)
class Endpoints:
    authorize: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"
    revoke: str = "https://oauth2.googleapis.com/revoke"
    drive: str = "https://www.googleapis.com/drive/v3"
    upload: str = "https://www.googleapis.com/upload/drive/v3"


@dataclass(frozen=True)
class SignInPages:
    """What the browser shows once Google has redirected back."""

    done: str = "Signed in. You can close this page."
    failed: str = "Not signed in. You can close this page."


def configured_client(
    environ: Mapping[str, str] = os.environ, path: Path | None = CLIENT_FILE
) -> GoogleClient | None:
    """The OAuth client: from the environment when both variables are set, else from the
    file a build put beside the package; None when there is neither."""
    client_id = environ.get(CLIENT_ID_ENV, "").strip()
    secret = environ.get(CLIENT_SECRET_ENV, "").strip()
    if client_id and secret:
        return GoogleClient(client_id, secret)
    if path is None:
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        client_id, secret = stored["client_id"], stored["client_secret"]
    except OSError, ValueError, KeyError, TypeError:
        return None
    if not (isinstance(client_id, str) and isinstance(secret, str) and client_id and secret):
        return None
    return GoogleClient(client_id, secret)


@dataclass(frozen=True)
class _Stored:
    account: str
    refresh_token: str

    def encode(self) -> str:
        return json.dumps(
            {"account": self.account, "refresh_token": self.refresh_token},
            separators=(",", ":"),
        )

    @staticmethod
    def decode(text: str | None) -> _Stored | None:
        if text is None:
            return None
        try:
            stored = json.loads(text)
            account, token = stored["account"], stored["refresh_token"]
        except ValueError, KeyError, TypeError:
            return None
        if not (isinstance(account, str) and isinstance(token, str) and token):
            return None
        return _Stored(account, token)


@dataclass(frozen=True)
class _Response:
    status: int
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise BackupError("cloud_failed", f"Google answered HTTP {self.status}") from error


def _send(request: urllib.request.Request, limit: int) -> _Response:
    """One request; a body past ``limit`` bytes is cut at ``limit + 1``."""
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return _Response(response.status, response.read(limit + 1))
    except urllib.error.HTTPError as error:
        with error:
            return _Response(error.code, error.read(_ERROR_BYTES))
    except urllib.error.URLError as error:
        raise BackupError("cloud_unreachable", str(error.reason)) from error
    except (OSError, http.client.HTTPException) as error:
        raise BackupError("cloud_unreachable", str(error) or type(error).__name__) from error


def _field(payload: Any, name: str) -> Any:
    """A field of a JSON object; None when there is no such object or field."""
    return payload.get(name) if isinstance(payload, dict) else None


def _oauth_error(response: _Response) -> tuple[str, BackupError]:
    """A refusal of the token endpoint, ``{"error": …, "error_description": …}``: its
    error code and the BackupError it makes."""
    try:
        payload = json.loads(response.body)
        error = str(payload.get("error", ""))
        description = str(payload.get("error_description", ""))
    except ValueError, AttributeError:
        error, description = "", ""
    if response.status >= 500:
        return error, BackupError("cloud_unreachable", f"Google answered HTTP {response.status}")
    detail = ": ".join(part for part in (error, description) if part)
    return error, BackupError("cloud_failed", detail or f"Google answered HTTP {response.status}")


def _drive_error(response: _Response) -> BackupError:
    """A Drive error: ``{"error": {"code", "message", "errors": [{"reason"}]}}``."""
    try:
        error = json.loads(response.body)["error"]
        message = str(error.get("message", ""))
        reasons = {str(e.get("reason")) for e in error.get("errors", [])}
    except ValueError, KeyError, TypeError, AttributeError:
        message, reasons = "", set()
    message = message or f"Google Drive answered HTTP {response.status}"
    if response.status == 401:
        return BackupError("not_signed_in", message)
    if reasons & _FULL:
        return BackupError("cloud_full", message)
    if response.status == 429 or response.status >= 500 or reasons & _LATER:
        return BackupError("cloud_unreachable", message)
    return BackupError("cloud_failed", message)


def _fit(key: str, value: str) -> str:
    """``value`` cut to what Drive keeps of an app property beside ``key``."""
    room = _PROPERTY_BYTES - len(key.encode())
    return value.encode()[:room].decode(errors="ignore")


def _properties(info: SnapshotInfo) -> dict[str, str]:
    values = {
        "created_at": str(info.created_at),
        "device": info.device,
        "app_version": info.app_version,
        "profiles": str(info.counts.profiles),
        "plans": str(info.counts.plans),
    }
    return {key: _fit(key, value) for key, value in values.items()}


def _entry(item: Any) -> BackupEntry | None:
    """A listed file as an entry; None for one that is not a snapshot of ours."""
    try:
        file_id, name, properties = item["id"], item["name"], item["appProperties"]
        size = int(item["size"])
        created_at = float(properties["created_at"])
        device, app_version = properties["device"], properties["app_version"]
        counts = Counts(int(properties["profiles"]), int(properties["plans"]))
    except TypeError, KeyError, ValueError:
        return None
    if not (
        isinstance(file_id, str)
        and _FILE_ID.fullmatch(file_id)
        and isinstance(name, str)
        and _NAME.fullmatch(name)
        and isinstance(device, str)
        and isinstance(app_version, str)
    ):
        return None
    if created_at.is_integer():
        created_at = int(created_at)
    return BackupEntry(file_id, SnapshotInfo(created_at, device, app_version, counts), size)


def _multipart(metadata: dict[str, Any], data: bytes) -> tuple[bytes, str]:
    """A multipart/related body of the file's metadata and content, and its content type."""
    boundary = f"solaris-{secrets.token_hex(16)}"
    head = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata, ensure_ascii=False)}\r\n"
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n"
    ).encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/related; boundary={boundary}"


class _Redirect:
    """The loopback listener's side of the sign-in: the first request carrying our ``state``
    settles ``code`` once the browser has its page; anything else (a favicon, a stray
    request) is turned away."""

    def __init__(self, state: str, pages: SignInPages) -> None:
        self.state = state
        self._pages = pages
        self.code: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(REDIRECT_READ_TIMEOUT_S):
                line = await reader.readline()
                while (await reader.readline()).strip():
                    pass
            method, target, _ = line.decode("latin-1").split(" ", 2)
            parsed = urlsplit(target)
            query = parse_qs(parsed.query)
            ours = method == "GET" and parsed.path == "/" and query.get("state") == [self.state]
            if not ours or self.code.done():
                await self._answer(writer, 404, "")
                return
            code, error = query.get("code", [""])[0], query.get("error", [""])[0]
            await self._answer(writer, 200, self._pages.done if code else self._pages.failed)
            if self.code.done():
                return
            if code:
                self.code.set_result(code)
            elif error == "access_denied":
                self.code.set_exception(BackupError("not_authorized", "access was denied"))
            else:
                self.code.set_exception(BackupError("cloud_failed", error or "no code"))
        except TimeoutError, ValueError, ConnectionError:
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    @staticmethod
    async def _answer(writer: asyncio.StreamWriter, status: int, text: str) -> None:
        body = (
            '<!doctype html><html><head><meta charset="utf-8"><title>Solaris</title></head>'
            '<body style="font: 15px system-ui, sans-serif; margin: 48px">'
            f"{html.escape(text)}</body></html>"
        ).encode()
        reason = "OK" if status == 200 else "Not Found"
        head = (
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        )
        writer.write(head.encode() + body)
        await writer.drain()


class GoogleDriveProvider:
    """BackupProvider over the Google account signed in through ``sign_in``. Its methods run
    on the asyncio thread; the requests on threads of their own."""

    kind = GOOGLE_DRIVE

    def __init__(
        self,
        client: GoogleClient,
        credentials: CredentialStore,
        *,
        endpoints: Endpoints | None = None,
        browser: Callable[[str], bool] = webbrowser.open,
        clock: Callable[[], float] = time.time,
        sign_in_timeout_s: float = SIGN_IN_TIMEOUT_S,
    ) -> None:
        """``browser`` opens Google's consent page and says whether it could."""
        self._client = client
        self._credentials = credentials
        self._endpoints = endpoints or Endpoints()
        self._browser = browser
        self._clock = clock
        self._sign_in_timeout_s = sign_in_timeout_s
        self._stored: _Stored | None = None
        self._loaded = False
        self._access: tuple[str, float] | None = None  # the token and when to refresh it
        self._token_lock = asyncio.Lock()

    @property
    def signed_in(self) -> bool:
        """Whether a sign-in is stored, as far as it has been read (``account``)."""
        return self._stored is not None

    async def _load(self) -> None:
        """Read the stored sign-in once. An unreadable store, such as a locked keychain,
        counts as none, and is read again next time."""
        if self._loaded:
            return
        try:
            text = await asyncio.to_thread(self._credentials.read, TOKEN_KEY)
        except CredentialError as error:
            _log.warning("could not read the Google sign-in: %s", error)
            return
        self._stored = _Stored.decode(text)
        self._loaded = True

    async def account(self) -> str | None:
        await self._load()
        return self._stored.account if self._stored else None

    # -- signing in and out ------------------------------------------------------------------
    async def sign_in(self, pages: SignInPages | None = None) -> str:
        """Open Google's consent page in the browser and wait for its redirect to the loopback
        listener, at most the sign-in timeout; cancelling stops the wait. The new sign-in
        replaces a stored one."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        redirect = _Redirect(secrets.token_urlsafe(32), pages or SignInPages())
        server = await asyncio.start_server(redirect.handle, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            redirect_uri = f"http://127.0.0.1:{port}"
            query = {
                "client_id": self._client.id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": redirect.state,
                # A refresh token on every sign-in, not only the first.
                "access_type": "offline",
                "prompt": "consent",
            }
            url = f"{self._endpoints.authorize}?{urlencode(query)}"
            if not await asyncio.to_thread(self._browser, url):
                raise BackupError("browser_failed", "could not open the browser")
            try:
                async with asyncio.timeout(self._sign_in_timeout_s):
                    code = await redirect.code
            except TimeoutError as error:
                raise BackupError("sign_in_timed_out", "no answer from the browser") from error
        finally:
            server.close()
            server.close_clients()
            await server.wait_closed()
        return await self._finish_sign_in(code, verifier, redirect_uri)

    async def _finish_sign_in(self, code: str, verifier: str, redirect_uri: str) -> str:
        response = await self._form(
            self._endpoints.token,
            {
                "client_id": self._client.id,
                "client_secret": self._client.secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        if response.status != 200:
            raise _oauth_error(response)[1]
        payload = response.json()
        access, refresh = _field(payload, "access_token"), _field(payload, "refresh_token")
        if SCOPE not in str(_field(payload, "scope") or "").split():
            raise BackupError("not_authorized", "access to the app's Drive data was not granted")
        if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh):
            raise BackupError("cloud_failed", "Google returned no refresh token")
        # The new grant's token stays out of this provider until the sign-in is stored.
        fields = urlencode({"fields": "user(displayName,emailAddress)"})
        authorization = {"Authorization": f"Bearer {access}"}
        url = f"{self._endpoints.drive}/about?{fields}"
        about = await self._request("GET", url, headers=authorization)
        if about.status != 200:
            raise _drive_error(about)
        user = _field(about.json(), "user")
        named = (_field(user, "emailAddress"), _field(user, "displayName"))
        account = next((n for n in named if isinstance(n, str) and n), DISPLAY_NAME)
        stored = _Stored(account, refresh)
        await asyncio.to_thread(self._credentials.write, TOKEN_KEY, stored.encode())
        self._stored, self._loaded = stored, True
        self._remember_access(access, _field(payload, "expires_in"))
        return account

    async def sign_out(self) -> None:
        """Revoke the grant and delete the stored sign-in; the snapshots stay. Google revokes
        the grant of the whole project, so other devices signed in to the same account must
        sign in again. A revocation that does not reach Google still deletes the token."""
        await self._load()
        stored = self._stored
        if stored is not None:
            try:
                response = await self._form(self._endpoints.revoke, {"token": stored.refresh_token})
                if response.status not in (200, 400):  # 400: revoked already
                    _log.warning("could not revoke the Google sign-in: HTTP %s", response.status)
            except BackupError as error:
                _log.warning("could not revoke the Google sign-in: %s", error)
        await asyncio.to_thread(self._credentials.delete, TOKEN_KEY)
        self._stored, self._loaded, self._access = None, True, None

    async def _forget(self, stored: _Stored) -> None:
        """Google refused the stored sign-in: delete it, so that the row asks to sign in
        again and automatic backup waits instead of failing every hour."""
        if self._stored is stored:
            self._stored, self._access = None, None
            with contextlib.suppress(CredentialError):
                await asyncio.to_thread(self._credentials.delete, TOKEN_KEY)

    # -- tokens and requests -----------------------------------------------------------------
    def _remember_access(self, token: str, expires_in: Any) -> None:
        lifetime = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
        self._access = (token, self._clock() + lifetime - EXPIRY_MARGIN_S)

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._access is not None and self._clock() < self._access[1]:
                return self._access[0]
            await self._load()
            stored = self._stored
            if stored is None:
                raise BackupError("not_signed_in", "sign in to Google Drive first")
            response = await self._form(
                self._endpoints.token,
                {
                    "client_id": self._client.id,
                    "client_secret": self._client.secret,
                    "grant_type": "refresh_token",
                    "refresh_token": stored.refresh_token,
                },
            )
            if response.status != 200:
                code, error = _oauth_error(response)
                if code == "invalid_grant":  # revoked, or expired
                    await self._forget(stored)
                    raise BackupError("not_signed_in", error.message)
                raise error
            payload = response.json()
            access = _field(payload, "access_token")
            if not isinstance(access, str) or not access:
                raise BackupError("cloud_failed", "Google returned no access token")
            self._remember_access(access, _field(payload, "expires_in"))
            return access

    async def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        limit: int = _JSON_BYTES,
    ) -> _Response:
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("User-Agent", USER_AGENT)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        return await _blocking(partial(_send, request, limit))

    async def _form(self, url: str, fields: dict[str, str]) -> _Response:
        body = urlencode(fields).encode()
        content = {"Content-Type": "application/x-www-form-urlencoded"}
        return await self._request("POST", url, body=body, headers=content)

    async def _drive(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        limit: int = _JSON_BYTES,
        missing_ok: bool = False,
    ) -> _Response:
        """A Drive request with an access token, refreshed and tried once more on a 401.
        Raises BackupError unless it succeeds, or answers 404 where ``missing_ok``."""
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {await self._access_token()}"}
            if content_type is not None:
                headers["Content-Type"] = content_type
            response = await self._request(method, url, body=body, headers=headers, limit=limit)
            if response.status == 401 and attempt == 0:
                self._access = None
                continue
            if 200 <= response.status < 300 or (missing_ok and response.status == 404):
                return response
            raise _drive_error(response)
        raise AssertionError("unreachable")

    # -- snapshots ---------------------------------------------------------------------------
    async def upload(self, snapshot: Snapshot) -> BackupEntry:
        stamp = datetime.fromtimestamp(snapshot.info.created_at, UTC).strftime("%Y%m%d-%H%M%S")
        data = snapshot.encode()
        metadata = {
            "name": f"solaris-backup-{stamp}-{snapshot.digest[:12]}.json",
            "parents": ["appDataFolder"],
            "mimeType": "application/json",
            "appProperties": _properties(snapshot.info),
        }
        body, content_type = _multipart(metadata, data)
        url = f"{self._endpoints.upload}/files?uploadType=multipart&fields=id"
        response = await self._drive("POST", url, body=body, content_type=content_type)
        file_id = _field(response.json(), "id")
        if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
            raise BackupError("cloud_failed", "Google Drive returned no file id")
        return BackupEntry(file_id, snapshot.info, len(data))

    async def list(self) -> Sequence[BackupEntry]:
        entries: list[BackupEntry] = []
        page: str | None = None
        while True:
            query = {
                "spaces": "appDataFolder",
                "q": "trashed = false",
                "fields": "nextPageToken,files(id,name,size,appProperties)",
                "pageSize": "100",
            }
            if page is not None:
                query["pageToken"] = page
            response = await self._drive("GET", f"{self._endpoints.drive}/files?{urlencode(query)}")
            payload = response.json()
            files = _field(payload, "files")
            entries += [e for e in map(_entry, files if isinstance(files, list) else []) if e]
            page = _field(payload, "nextPageToken")
            if not isinstance(page, str) or not page:
                break
        return sorted(entries, key=lambda e: (e.info.created_at, e.id), reverse=True)

    def _file_url(self, base: str, entry_id: str) -> str:
        if not _FILE_ID.fullmatch(entry_id):  # no other id is a Drive file of ours
            raise BackupError("backup_missing", f"no snapshot {entry_id}")
        return f"{base}/files/{quote(entry_id, safe='')}"

    async def download(self, entry_id: str) -> Snapshot:
        url = self._file_url(self._endpoints.drive, entry_id) + "?alt=media"
        response = await self._drive("GET", url, limit=MAX_SNAPSHOT_BYTES, missing_ok=True)
        if response.status == 404:
            raise BackupError("backup_missing", f"no snapshot {entry_id}")
        return Snapshot.decode(response.body)

    async def delete(self, entry_id: str) -> None:
        if _FILE_ID.fullmatch(entry_id):
            url = self._file_url(self._endpoints.drive, entry_id)
            await self._drive("DELETE", url, missing_ok=True)


def configured_provider(credentials: CredentialStore) -> GoogleDriveProvider | None:
    """The provider of the configured OAuth client; None without one."""
    client = configured_client()
    return GoogleDriveProvider(client, credentials) if client else None
