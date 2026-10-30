"""A stand-in for Google on 127.0.0.1: the OAuth token and revoke endpoints, the Drive v3
calls models/backup_google.py makes, and a browser whose user consents (or not) and follows
the redirect to the provider's loopback listener. The tests never reach Google."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import HTTP
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from solaris.models.backup_google import (
    SCOPE,
    Endpoints,
    GoogleClient,
    GoogleDriveProvider,
)
from solaris.models.credentials import CredentialStore

CLIENT = GoogleClient("test-client.apps.googleusercontent.com", "test-client-secret")
QUOTA_EXCEEDED = (
    403,
    {
        "error": {
            "code": 403,
            "message": "The user's Drive storage quota has been exceeded.",
            "errors": [{"reason": "storageQuotaExceeded"}],
        }
    },
)
# The browser follows redirects to the loopback listener without any proxy.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class FakeFile:
    name: str
    parents: list[str]
    properties: dict[str, str]
    content: bytes


@dataclass
class _Grant:
    challenge: str
    redirect_uri: str
    scope: str


@dataclass
class Consent:
    """What the browser saw: the consent page's query and the loopback page's answer."""

    query: dict[str, str]
    status: int = 0
    page: str = ""
    done: threading.Event = field(default_factory=threading.Event)


class FakeGoogle:
    def __init__(self, account: str = "user@example.com") -> None:
        self.account = account
        self.files: dict[str, FakeFile] = {}
        self.refresh_tokens: set[str] = set()
        self.access_tokens: set[str] = set()
        self.revoked: list[str] = []
        self.calls: list[str] = []
        self.failures: dict[str, tuple[int, Any]] = {}  # by call, answered once
        self.broken: tuple[int, Any] | None = None  # answered by every upload
        self.page_size = 100
        self.consents: list[Consent] = []
        # The user at the browser: opens it, consents, grants this scope, follows through.
        self.opens = True
        self.consents_to = True
        self.granted_scope = SCOPE
        self.follows = True
        self.gate = threading.Event()  # an upload waits until it is set
        self.gate.set()
        self._grants: dict[str, _Grant] = {}
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake._serve(self, "GET")

            def do_POST(self) -> None:
                fake._serve(self, "POST")

            def do_DELETE(self) -> None:
                fake._serve(self, "DELETE")

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def endpoints(self) -> Endpoints:
        return Endpoints(
            authorize=f"{self.base}/o/oauth2/v2/auth",
            token=f"{self.base}/token",
            revoke=f"{self.base}/revoke",
            drive=f"{self.base}/drive/v3",
            upload=f"{self.base}/upload/drive/v3",
        )

    def provider(self, credentials: CredentialStore, **options: Any) -> GoogleDriveProvider:
        """A provider of the test client that reaches this server through this browser."""
        options.setdefault("endpoints", self.endpoints)
        options.setdefault("browser", self.browser)
        return GoogleDriveProvider(CLIENT, credentials, **options)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def expire_access(self) -> None:
        """Every access token stops working, as an hour later."""
        with self._lock:
            self.access_tokens.clear()

    def revoke_all(self) -> None:
        """The grant goes, as when the user removes the app from the Google account."""
        with self._lock:
            self.refresh_tokens.clear()
            self.access_tokens.clear()

    def snapshots(self) -> list[FakeFile]:
        with self._lock:
            return [f for f in self.files.values() if f.parents == ["appDataFolder"]]

    def add_file(self, name: str, content: bytes, properties: dict[str, str] | None = None) -> str:
        """A file in appDataFolder that the provider did not upload."""
        file_id = secrets.token_urlsafe(16)
        with self._lock:
            self.files[file_id] = FakeFile(name, ["appDataFolder"], properties or {}, content)
        return file_id

    # -- the browser -------------------------------------------------------------------------
    def browser(self, url: str) -> bool:
        """Open the consent page: the user consents or declines, and Google redirects to the
        loopback listener, which the browser then shows."""
        if not self.opens:
            return False
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == self.endpoints.authorize
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        consent = Consent(query)
        with self._lock:
            self.consents.append(consent)
        if not self.follows:
            return True  # the user walks away
        redirect = query["redirect_uri"]
        answer: dict[str, str] = {"state": query["state"]}
        if self.consents_to:
            code = secrets.token_urlsafe(24)
            with self._lock:
                self._grants[code] = _Grant(query["code_challenge"], redirect, self.granted_scope)
            answer["code"] = code
        else:
            answer["error"] = "access_denied"

        def follow() -> None:
            try:
                with _DIRECT.open(f"{redirect}/?{urlencode(answer)}", timeout=10) as response:
                    consent.status, consent.page = response.status, response.read().decode()
            finally:
                consent.done.set()

        threading.Thread(target=follow, daemon=True).start()
        return True

    # -- the server --------------------------------------------------------------------------
    def _serve(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(handler.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        call, answer = self._route(handler, method, parts.path, query, body)
        with self._lock:
            self.calls.append(call)
            failure = self.failures.pop(call, None)
        if call == "upload":
            self.gate.wait(10)
            failure = failure or self.broken
        status, payload = failure or answer()
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        handler.send_response(status)
        if status != 204:
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if status != 204:
            handler.wfile.write(data)

    def _route(
        self,
        handler: BaseHTTPRequestHandler,
        method: str,
        path: str,
        query: dict[str, str],
        body: bytes,
    ) -> tuple[str, Callable[[], tuple[int, Any]]]:
        """The call a request makes, and how to answer it unless it is to fail."""
        if method == "POST" and path == "/token":
            return "token", partial(self._token, parse_qs(body.decode()))
        if method == "POST" and path == "/revoke":
            return "revoke", partial(self._revoke, parse_qs(body.decode()))
        if not self._authorized(handler):
            return "unauthorized", partial(_answer, 401, "Invalid Credentials", "authError")
        if method == "GET" and path == "/drive/v3/about":
            return "about", self._about
        if method == "POST" and path == "/upload/drive/v3/files":
            content_type = handler.headers.get("Content-Type", "")
            return "upload", partial(self._upload, query, content_type, body)
        if method == "GET" and path == "/drive/v3/files":
            return "list", partial(self._list, query)
        if path.startswith("/drive/v3/files/"):
            file_id = path.removeprefix("/drive/v3/files/")
            if method == "GET":
                return "download", partial(self._download, file_id, query)
            return "delete", partial(self._delete, file_id)
        return "unknown", partial(_answer, 404, "Not Found", "notFound")

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        header = handler.headers.get("Authorization", "")
        with self._lock:
            return header.startswith("Bearer ") and header[7:] in self.access_tokens

    def _new_access(self) -> str:
        token = f"access-{secrets.token_urlsafe(16)}"
        self.access_tokens.add(token)
        return token

    def _about(self) -> tuple[int, Any]:
        return 200, {"user": {"emailAddress": self.account, "displayName": "User"}}

    def _token(self, form: dict[str, list[str]]) -> tuple[int, Any]:
        fields = {k: v[0] for k, v in form.items()}
        if fields.get("client_id") != CLIENT.id or fields.get("client_secret") != CLIENT.secret:
            return 401, {"error": "invalid_client", "error_description": "Unauthorized"}
        with self._lock:
            if fields.get("grant_type") == "refresh_token":
                if fields.get("refresh_token") not in self.refresh_tokens:
                    return 400, {
                        "error": "invalid_grant",
                        "error_description": "Token has been expired or revoked.",
                    }
                return 200, {"access_token": self._new_access(), "expires_in": 3599}
            grant = self._grants.pop(fields.get("code", ""), None)
            verifier = fields.get("code_verifier", "").encode()
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=")
            if (
                grant is None
                or grant.redirect_uri != fields.get("redirect_uri")
                or grant.challenge != digest.decode()
            ):
                return 400, {"error": "invalid_grant", "error_description": "Bad Request"}
            refresh = f"refresh-{secrets.token_urlsafe(16)}"
            self.refresh_tokens.add(refresh)
            return 200, {
                "access_token": self._new_access(),
                "expires_in": 3599,
                "refresh_token": refresh,
                "scope": grant.scope,
                "token_type": "Bearer",
            }

    def _revoke(self, form: dict[str, list[str]]) -> tuple[int, Any]:
        token = form.get("token", [""])[0]
        with self._lock:
            self.revoked.append(token)
            if token not in self.refresh_tokens:
                return 400, {"error": "invalid_token"}
            # Google revokes the grant: every token of it.
            self.refresh_tokens.clear()
            self.access_tokens.clear()
        return 200, {}

    def _upload(self, query: dict[str, str], content_type: str, body: bytes) -> tuple[int, Any]:
        assert query.get("uploadType") == "multipart"
        message = BytesParser(policy=HTTP).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body
        )
        described, content = (part.get_payload(decode=True) for part in message.iter_parts())
        assert isinstance(described, bytes)
        assert isinstance(content, bytes)
        metadata = json.loads(described)
        file_id = secrets.token_urlsafe(16)
        stored = FakeFile(
            metadata["name"], metadata["parents"], metadata.get("appProperties", {}), content
        )
        for key, value in stored.properties.items():
            assert len(key.encode()) + len(value.encode()) <= 124
        with self._lock:
            self.files[file_id] = stored
        return 200, {"id": file_id}

    def _list(self, query: dict[str, str]) -> tuple[int, Any]:
        assert query.get("spaces") == "appDataFolder"
        with self._lock:
            listed = [
                {
                    "id": file_id,
                    "name": f.name,
                    "size": str(len(f.content)),
                    **({"appProperties": f.properties} if f.properties else {}),
                }
                for file_id, f in self.files.items()
                if f.parents == ["appDataFolder"]
            ]
        start = int(query.get("pageToken", "0"))
        page = listed[start : start + self.page_size]
        payload: dict[str, Any] = {"files": page}
        if start + self.page_size < len(listed):
            payload["nextPageToken"] = str(start + self.page_size)
        return 200, payload

    def _download(self, file_id: str, query: dict[str, str]) -> tuple[int, Any]:
        assert query.get("alt") == "media"
        with self._lock:
            found = self.files.get(file_id)
        if found is None:
            return _answer(404, f"File not found: {file_id}.", "notFound")
        return 200, found.content

    def _delete(self, file_id: str) -> tuple[int, Any]:
        with self._lock:
            found = self.files.pop(file_id, None)
        if found is None:
            return _answer(404, f"File not found: {file_id}.", "notFound")
        return 204, b""


def _answer(code: int, message: str, reason: str) -> tuple[int, Any]:
    """A Drive error as Google answers it."""
    return code, {"error": {"code": code, "message": message, "errors": [{"reason": reason}]}}
