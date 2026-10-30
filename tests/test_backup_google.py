"""The Google Drive backup provider against a stand-in for Google on 127.0.0.1 (docs/storage.md
§4): the OAuth sign-in with PKCE through a loopback redirect, the token in the credential
store, and the Drive calls. The provider contract itself is in test_backup_providers.py."""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Coroutine, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import build_app
from scripts.build_apple import BuildError
from solaris.models.backup import BackupError, Backups
from solaris.models.backup_auto import AutoBackup, BackupTargets
from solaris.models.backup_google import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    GOOGLE_DRIVE,
    SCOPE,
    TOKEN_KEY,
    Endpoints,
    GoogleClient,
    SignInPages,
    configured_client,
)
from solaris.models.credentials import CredentialError
from solaris.models.profiles import KIND_SUBSCRIPTION, Profile, ProfileList
from solaris.models.store import ProfileStore
from tests.support.credentials import MemoryCredentials
from tests.support.google import CLIENT, QUOTA_EXCEEDED, FakeGoogle

PAGES = SignInPages("已登录", "没有登录")


def run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGoogle]:
    # Reach the loopback server directly even if the machine has a proxy configured.
    monkeypatch.setenv("no_proxy", "*")
    fake = FakeGoogle()
    yield fake
    fake.close()


class Data:
    def __init__(self, directory: Path, device: str = "laptop") -> None:
        self.now = 1_800_000_000.0
        self.store = ProfileStore(directory, clock=lambda: self.now)
        self.backups = Backups(self.store.database, device=device, app_version="1.0.0")
        self.names = 0

    def change(self) -> None:
        self.names += 1
        self.now += 60
        profile = Profile(
            id="a", name=f"订阅 {self.names}", kind=KIND_SUBSCRIPTION, url="https://a.example/s"
        )
        self.store.save(ProfileList((profile,), "a"))


def refused(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


# -- the client --------------------------------------------------------------------------------
def test_the_client_comes_from_the_environment_or_the_build(tmp_path: Path) -> None:
    bundled = tmp_path / "google_client.json"
    environ = {CLIENT_ID_ENV: " id.apps.googleusercontent.com ", CLIENT_SECRET_ENV: "secret"}
    assert configured_client(environ, bundled) == GoogleClient(
        "id.apps.googleusercontent.com", "secret"
    )
    assert configured_client({}, bundled) is None  # neither: no Google Drive
    assert configured_client({CLIENT_ID_ENV: "id"}, bundled) is None  # Google needs both
    bundled.write_text(json.dumps({"client_id": "b", "client_secret": "s"}), encoding="utf-8")
    assert configured_client({}, bundled) == GoogleClient("b", "s")
    assert configured_client(environ, bundled) == GoogleClient(
        "id.apps.googleusercontent.com", "secret"
    )
    for broken in ("{", "[]", json.dumps({"client_id": "b"}), json.dumps({"client_id": 1})):
        bundled.write_text(broken, encoding="utf-8")
        assert configured_client({}, bundled) is None


def test_a_build_bundles_the_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(CLIENT_SECRET_ENV, raising=False)
    assert build_app.google_client(tmp_path) == []  # no client: no Google Drive
    monkeypatch.setenv(CLIENT_ID_ENV, "id.apps.googleusercontent.com")
    with pytest.raises(BuildError):
        build_app.google_client(tmp_path)  # half a client
    monkeypatch.setenv(CLIENT_SECRET_ENV, "secret")
    (option,) = build_app.google_client(tmp_path)
    bundled = tmp_path / "google_client.json"
    assert option == f"--include-data-files={bundled}=solaris/google_client.json"
    assert configured_client({}, bundled) == GoogleClient("id.apps.googleusercontent.com", "secret")


# -- signing in and out ------------------------------------------------------------------------
def test_signing_in_through_the_browser(google: FakeGoogle) -> None:
    credentials = MemoryCredentials()
    provider = google.provider(credentials)

    async def scenario() -> None:
        assert await provider.account() is None
        assert not provider.signed_in
        assert await provider.sign_in(PAGES) == "user@example.com"

    run(scenario)
    (consent,) = google.consents
    assert consent.done.wait(5)
    query = consent.query
    assert query["client_id"] == CLIENT.id
    assert query["scope"] == SCOPE  # nothing but the app's own data
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert len(query["state"]) >= 32
    redirect = urlsplit(query["redirect_uri"])
    assert (redirect.scheme, redirect.hostname, redirect.path) == ("http", "127.0.0.1", "")
    assert redirect.port is not None
    assert refused(redirect.port)  # the listener is gone
    assert (consent.status, "已登录" in consent.page) == (200, True)

    # The refresh token is in the credential store, and nowhere else of ours.
    stored = json.loads(credentials.secrets[TOKEN_KEY])
    assert stored == {"account": "user@example.com", "refresh_token": stored["refresh_token"]}
    assert stored["refresh_token"] in google.refresh_tokens
    assert list(credentials.secrets) == [TOKEN_KEY]
    assert provider.signed_in
    assert run(provider.account) == "user@example.com"
    # Another start reads it from the store.
    again = google.provider(credentials)
    assert run(again.account) == "user@example.com"


def test_declining_failing_or_walking_away(google: FakeGoogle) -> None:
    credentials = MemoryCredentials()

    def sign_in(**options: Any) -> str:
        provider = google.provider(credentials, **options)
        with pytest.raises(BackupError) as raised:
            run(lambda: provider.sign_in(PAGES))
        assert not provider.signed_in
        return raised.value.code

    google.consents_to = False
    assert sign_in() == "not_authorized"
    assert google.consents[-1].done.wait(5)
    assert "没有登录" in google.consents[-1].page
    google.consents_to = True
    google.granted_scope = "openid"  # the user left the Drive box unticked
    assert sign_in() == "not_authorized"
    google.granted_scope = SCOPE
    google.follows = False
    assert sign_in(sign_in_timeout_s=0.2) == "sign_in_timed_out"
    port = urlsplit(google.consents[-1].query["redirect_uri"]).port
    assert port is not None
    assert refused(port)
    google.opens = False
    assert sign_in() == "browser_failed"
    google.opens = google.follows = True
    google.failures["about"] = (503, {"error": {"code": 503, "message": "Backend Error"}})
    assert sign_in() == "cloud_unreachable"  # the account could not be read: nothing kept
    assert credentials.secrets == {}


def test_cancelling_the_sign_in_closes_the_listener(google: FakeGoogle) -> None:
    google.follows = False
    provider = google.provider(MemoryCredentials())

    async def scenario() -> None:
        task = asyncio.create_task(provider.sign_in(PAGES))
        while not google.consents:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario)
    port = urlsplit(google.consents[0].query["redirect_uri"]).port
    assert port is not None
    assert refused(port)


def test_the_listener_turns_other_requests_away(google: FakeGoogle) -> None:
    turned_away: list[int] = []

    def browser(url: str) -> bool:
        # A request without our state, such as another page's, comes first.
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in ("/?code=forged&state=wrong", "/favicon.ico"):
            try:
                direct.open(redirect + path, timeout=5)
            except urllib.error.HTTPError as error:
                turned_away.append(error.code)
        return google.browser(url)

    provider = google.provider(MemoryCredentials(), browser=browser)
    assert run(lambda: provider.sign_in(PAGES)) == "user@example.com"
    assert turned_away == [404, 404]


def test_a_locked_credential_store(google: FakeGoogle) -> None:
    credentials = MemoryCredentials()
    credentials.fail = True
    provider = google.provider(credentials)
    assert run(provider.account) is None  # read as none, and read again later
    with pytest.raises(CredentialError):
        run(lambda: provider.sign_in(PAGES))
    assert not provider.signed_in
    credentials.fail = False
    assert run(lambda: provider.sign_in(PAGES)) == "user@example.com"


def test_signing_out_revokes_the_grant(google: FakeGoogle, tmp_path: Path) -> None:
    credentials, elsewhere = MemoryCredentials(), MemoryCredentials()
    provider, other = google.provider(credentials), google.provider(elsewhere)
    data = Data(tmp_path)
    data.change()
    run(lambda: provider.sign_in(PAGES))
    run(lambda: other.sign_in(PAGES))  # another device, the same account
    run(lambda: provider.upload(data.backups.snapshot()))
    token = json.loads(credentials.secrets[TOKEN_KEY])["refresh_token"]

    run(provider.sign_out)
    assert google.revoked == [token]
    assert credentials.secrets == {}
    assert not provider.signed_in
    assert run(provider.account) is None
    assert len(google.snapshots()) == 1  # the snapshots stay
    with pytest.raises(BackupError) as raised:
        run(provider.list)
    assert raised.value.code == "not_signed_in"

    # Google revokes the whole grant: the other device must sign in again.
    google.expire_access()
    with pytest.raises(BackupError) as raised:
        run(other.list)
    assert raised.value.code == "not_signed_in"
    assert elsewhere.secrets == {}  # the refused token is gone too
    assert not other.signed_in


def test_signing_out_offline_still_forgets(google: FakeGoogle) -> None:
    credentials = MemoryCredentials()
    run(lambda: google.provider(credentials).sign_in(PAGES))
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    offline = google.provider(credentials, endpoints=Endpoints(revoke=f"http://127.0.0.1:{port}"))
    run(offline.sign_out)
    assert credentials.secrets == {}
    assert google.revoked == []


# -- Drive -------------------------------------------------------------------------------------
@pytest.fixture
def signed_in(google: FakeGoogle) -> Any:
    provider = google.provider(MemoryCredentials())
    run(lambda: provider.sign_in(PAGES))
    return provider


def test_snapshots_carry_their_info(google: FakeGoogle, signed_in: Any, tmp_path: Path) -> None:
    data = Data(tmp_path, device="一台名字很长的电脑" * 8)
    data.change()
    snapshot = data.backups.snapshot()
    entry = run(lambda: signed_in.upload(snapshot))

    (stored,) = google.snapshots()
    assert stored.name == f"solaris-backup-20270115-080100-{snapshot.digest[:12]}.json"
    assert stored.content == snapshot.encode()
    assert stored.properties["profiles"] == "1"
    assert (entry.info, entry.size) == (snapshot.info, len(snapshot.encode()))
    (listed,) = run(signed_in.list)
    assert listed.id == entry.id
    assert listed.size == entry.size
    # Drive keeps 124 bytes of key and value: a long device name is cut on a character.
    assert snapshot.info.device.startswith(listed.info.device)
    assert len(("device" + listed.info.device).encode()) <= 124
    assert listed.info.created_at == snapshot.info.created_at
    assert listed.info.counts == snapshot.info.counts
    assert run(lambda: signed_in.download(entry.id)) == snapshot


def test_listing_pages_and_skips_other_files(
    google: FakeGoogle, signed_in: Any, tmp_path: Path
) -> None:
    data = Data(tmp_path)
    google.page_size = 2
    for _ in range(5):
        data.change()
        run(lambda: signed_in.upload(data.backups.snapshot()))
    google.add_file("notes.txt", b"hello", {"device": "x"})
    google.add_file("solaris-backup-20270115-080000-0123456789ab.json", b"{}")  # no info
    listed = run(signed_in.list)
    assert len(listed) == 5
    times = [e.info.created_at for e in listed]
    assert times == sorted(times, reverse=True)
    assert google.calls.count("list") == 4  # seven files, two a page


def test_drive_failures(google: FakeGoogle, signed_in: Any, tmp_path: Path) -> None:
    data = Data(tmp_path)
    data.change()
    snapshot = data.backups.snapshot()

    def code(call: Callable[[], Coroutine[Any, Any, Any]]) -> str:
        with pytest.raises(BackupError) as raised:
            run(call)
        return raised.value.code

    google.failures["upload"] = QUOTA_EXCEEDED
    assert code(lambda: signed_in.upload(snapshot)) == "cloud_full"
    google.failures["upload"] = (503, {"error": {"code": 503, "message": "Backend Error"}})
    assert code(lambda: signed_in.upload(snapshot)) == "cloud_unreachable"
    google.failures["list"] = (403, {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})
    assert code(signed_in.list) == "cloud_unreachable"
    google.failures["list"] = (400, {"error": {"code": 400, "message": "Invalid Value"}})
    assert code(signed_in.list) == "cloud_failed"
    assert code(lambda: signed_in.download("missing")) == "backup_missing"
    calls = len(google.calls)
    assert code(lambda: signed_in.download("../about")) == "backup_missing"  # not asked
    run(lambda: signed_in.delete("../about"))
    assert len(google.calls) == calls
    run(lambda: signed_in.delete("missing"))  # gone already: no error

    unreachable = google.provider(MemoryCredentials())
    run(lambda: unreachable.sign_in(PAGES))
    google.close()
    assert code(unreachable.list) == "cloud_unreachable"


def test_an_expired_access_token_is_refreshed(google: FakeGoogle, signed_in: Any) -> None:
    run(signed_in.list)
    tokens = google.calls.count("token")
    google.expire_access()  # Drive answers 401 once
    assert run(signed_in.list) == []
    assert google.calls.count("token") == tokens + 1
    assert google.calls.count("unauthorized") == 1


def test_a_revoked_grant_waits_for_a_new_sign_in(google: FakeGoogle, tmp_path: Path) -> None:
    credentials = MemoryCredentials()
    provider = google.provider(credentials)
    run(lambda: provider.sign_in(PAGES))
    google.revoke_all()  # removed from the Google account's third-party access
    with pytest.raises(BackupError) as raised:
        run(provider.list)
    assert raised.value.code == "not_signed_in"
    assert credentials.secrets == {}
    assert not provider.signed_in


def test_tokens_stay_out_of_the_database_and_the_snapshots(
    google: FakeGoogle, tmp_path: Path
) -> None:
    credentials = MemoryCredentials()
    provider = google.provider(credentials)
    data = Data(tmp_path / "data")
    targets = BackupTargets(data.store.database)
    account = run(lambda: provider.sign_in(PAGES))
    targets.add(GOOGLE_DRIVE, account)
    data.change()
    auto = AutoBackup(data.backups, targets, lambda _: provider)
    (outcome,) = run(auto.backup)
    assert outcome.uploaded
    token = json.loads(credentials.secrets[TOKEN_KEY])["refresh_token"]
    data.store.close()
    written = b"".join(p.read_bytes() for p in (tmp_path / "data").iterdir() if p.is_file())
    assert token.encode() not in written
    assert all(token.encode() not in f.content for f in google.snapshots())
    assert not any(token.startswith(t) for t in google.access_tokens)


def test_automatic_backup_keeps_the_newest_ten(google: FakeGoogle, tmp_path: Path) -> None:
    provider = google.provider(MemoryCredentials())
    data = Data(tmp_path)
    targets = BackupTargets(data.store.database)
    targets.add(GOOGLE_DRIVE, run(lambda: provider.sign_in(PAGES)))
    auto = AutoBackup(data.backups, targets, lambda _: provider, clock=lambda: data.now)
    for _ in range(12):
        data.change()
        run(auto.backup)
    entries = run(provider.list)
    assert len(entries) == len(google.snapshots()) == 10
    assert entries[-1].info.created_at == 1_800_000_000 + 3 * 60
