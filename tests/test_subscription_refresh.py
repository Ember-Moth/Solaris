"""Exercise HTTP response metadata, conditional requests and the SQLite cache together."""

import asyncio
import base64
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from pytestqt.qtbot import QtBot

from solaris.host.base import HostError
from solaris.models.library import ProfileLibrary
from solaris.models.profiles import ProfileError, Usage
from solaris.models.store import ProfileStore
from solaris.models.subscription import UrlFetcher
from tests.support.simulated import SAMPLE_CONFIG, SimulatedHost
from tests.support.window import Loaded, kind


class Provider:
    def __init__(self) -> None:
        self.headers = {
            "ETag": 'W/"first"',
            "Profile-Title": "base64:" + base64.b64encode("测试订阅".encode()).decode(),
            "Profile-Update-Interval": "6",
            "Profile-Web-Page-Url": "https://site.example/home",
            "Support-Url": "https://site.example/help",
            "X-Cos-Meta-Subscription-Userinfo": "upload=1; download=2; total=30",
        }
        self.body = SAMPLE_CONFIG.encode()
        self.redirect = "/v1"
        self.requests: list[tuple[str, str | None]] = []
        self.arrived = Event()
        self.release = Event()
        self.release.set()
        self.force_304 = False
        self.status = 200
        self.url = ""


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[Provider]:
    state = Provider()
    monkeypatch.setenv("no_proxy", "*")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            tag = self.headers.get("If-None-Match")
            state.requests.append((self.path, tag))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", state.redirect)
                self.end_headers()
                return
            unchanged = tag is not None and tag == state.headers.get("ETag")
            state.arrived.set()
            state.release.wait(5)
            self.send_response(304 if unchanged or state.force_304 else state.status)
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if not unchanged and not state.force_304:
                self.wfile.write(state.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        state.url = f"http://127.0.0.1:{httpd.server_port}"
        thread = Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield state
        finally:
            state.release.set()
            httpd.shutdown()
            thread.join(5)


def test_conditional_refresh_across_restart_redirect_and_source_edit(
    provider: Provider, tmp_path: Path
) -> None:
    now = 10_000.0
    host = SimulatedHost()
    store = ProfileStore(tmp_path)
    library = ProfileLibrary(store, host, UrlFetcher(), clock=lambda: now)
    url = provider.url + "/redirect"
    (profile,) = asyncio.run(library.profiles.import_url(url)).profiles
    assert (profile.name, profile.auto_update_hours) == ("测试订阅", 6)
    assert (profile.website, profile.support) == (
        "https://site.example/home",
        "https://site.example/help",
    )
    store.close()
    store = ProfileStore(tmp_path)
    library = ProfileLibrary(store, host, UrlFetcher(), clock=lambda: now)
    try:
        assert library.snapshot().get(profile.id) == profile
        provider.headers["X-Cos-Meta-Subscription-Userinfo"] = "download=4; total=30"
        provider.headers.pop("Support-Url")
        provider.headers["Profile-Web-Page-Url"] = "https://site.example/new"
        now += 6 * 3600
        reloads = host.reloads
        refreshed = asyncio.run(library.profiles.update_due())
        assert provider.requests[-2:] == [("/redirect", None), ("/v1", 'W/"first"')]
        assert not refreshed.changed
        assert refreshed.profiles.get(profile.id).updated_at == now
        assert refreshed.profiles.get(profile.id).usage == Usage(0, 4, 30, 0)
        assert store.read_config(profile.id) == SAMPLE_CONFIG
        assert refreshed.profiles.get(profile.id).website == "https://site.example/new"
        assert refreshed.profiles.get(profile.id).support == profile.support
        assert host.reloads == reloads
        queries = len(provider.requests)
        asyncio.run(library.profiles.update_due())
        assert len(provider.requests) == queries

        # Keep the subscription URL, but its redirect now leads to another representation.
        provider.redirect = "/v2"
        provider.headers.update(
            {"ETag": '"second"', "Profile-Title": "Other", "Profile-Update-Interval": "24"}
        )
        asyncio.run(library.profiles.edit(profile.id, "Mine", url, True, interval_hours=12))
        result = asyncio.run(library.profiles.update(profile.id))
        assert provider.requests[-2:] == [("/redirect", None), ("/v2", None)]
        assert (
            result.profiles.get(profile.id).name,
            result.profiles.get(profile.id).auto_update_hours,
        ) == ("Mine", 12)
        # Failed validation of the new representation must preserve both the old bytes and tag.
        provider.headers["ETag"] = '"invalid"'
        provider.body = b"not a valid profile"
        with pytest.raises(HostError):
            asyncio.run(library.profiles.update(profile.id))
        assert store.read_config(profile.id) == SAMPLE_CONFIG
        saved = store.cached_etag(profile.id, url)
        assert saved is not None
        assert saved.value == '"second"'
        assert library.snapshot().get(profile.id).name == "Mine"

        # Missing response metadata on a later 200 clears obsolete links and validators.
        provider.headers.clear()
        provider.body = SAMPLE_CONFIG.encode()
        updated = asyncio.run(library.profiles.update(profile.id)).profiles.get(profile.id)
        assert store.cached_etag(profile.id, url) is None
        assert (updated.website, updated.support) == ("", "")
        provider.headers["ETag"] = '"third"'
        asyncio.run(library.profiles.update(profile.id))
        replacement = provider.url + "/changed"
        asyncio.run(library.profiles.edit(profile.id, "Mine", replacement, False))
        assert store.cached_etag(profile.id, replacement) is None
        updated = asyncio.run(library.profiles.update(profile.id)).profiles.get(profile.id)
        assert provider.requests[-1] == ("/changed", None)
        assert updated.auto_update_hours == -12
        with store.database.transaction() as conn:
            conn.execute("DELETE FROM profile_contents WHERE profile_id = ?", (profile.id,))
        asyncio.run(library.profiles.update(profile.id))
        assert provider.requests[-1] == ("/changed", None)
        assert store.read_config(profile.id) == SAMPLE_CONFIG
    finally:
        store.close()


@pytest.mark.parametrize(
    ("headers", "name", "hours", "expected"),
    [
        ({}, "", None, ("127.0.0.1", 12)),
        ({"Profile-Title": "Café"}, "", None, ("Café", 12)),
        ({"Profile-Title": "名称".encode().decode("latin1")}, "", None, ("名称", 12)),
        (
            {
                "Profile-Title": "base64:!",
                "Profile-Update-Interval": "0",
                "Content-Disposition": 'attachment; filename="fallback.yaml"',
            },
            "",
            None,
            ("fallback", 12),
        ),
        (
            {"Profile-Title": "plain", "Profile-Update-Interval": "2147483648"},
            "",
            None,
            ("plain", 12),
        ),
        ({"Profile-Title": "ignored", "Profile-Update-Interval": "6"}, "Mine", 12, ("Mine", 12)),
    ],
)
def test_import_precedence_and_invalid_links(
    provider: Provider,
    tmp_path: Path,
    headers: dict[str, str],
    name: str,
    hours: int | None,
    expected: tuple[str, int],
) -> None:
    provider.headers = {
        **headers,
        "Support-Url": "file:///bad",
        "Profile-Web-Page-Url": "https://[invalid",
    }
    store = ProfileStore(tmp_path)
    try:
        library = ProfileLibrary(store, SimulatedHost(), UrlFetcher())
        (profile,) = asyncio.run(
            library.profiles.import_url(provider.url, name, interval_hours=hours)
        ).profiles
        assert (profile.name, profile.auto_update_hours) == expected
        assert (profile.website, profile.support) == ("", "")
    finally:
        store.close()


def test_late_304_from_old_source_does_not_redate_new_address(
    provider: Provider, tmp_path: Path
) -> None:
    host = SimulatedHost()
    store = ProfileStore(tmp_path)
    library = ProfileLibrary(store, host, UrlFetcher())

    async def scenario() -> None:
        (profile,) = (
            await library.profiles.import_url(provider.url + "/v1", auto_update=False)
        ).profiles
        assert profile.auto_update_hours <= 0
        provider.arrived.clear()
        provider.release.clear()
        task = asyncio.create_task(library.profiles.update(profile.id))
        try:
            assert await asyncio.to_thread(provider.arrived.wait, 4)
            await library.profiles.edit(profile.id, "New", provider.url + "/v2", False)
        finally:
            provider.release.set()
            await task
        assert provider.requests[-1] == ("/v1", 'W/"first"')
        assert store.load().get(profile.id).updated_at == 0
        assert store.load().get(profile.id).website == ""

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_unsolicited_304_remains_a_download_error(provider: Provider) -> None:
    provider.force_304 = True
    with pytest.raises(ProfileError, match="HTTP 304"):
        asyncio.run(UrlFetcher().fetch(provider.url))


def test_cache_removed_during_a_304_is_fetched_unconditionally(
    provider: Provider, tmp_path: Path
) -> None:
    store = ProfileStore(tmp_path)
    library = ProfileLibrary(store, SimulatedHost(), UrlFetcher())

    async def scenario() -> None:
        (profile,) = (await library.profiles.import_url(provider.url + "/v1")).profiles
        provider.arrived.clear()
        provider.release.clear()
        task = asyncio.create_task(library.profiles.update(profile.id))
        try:
            assert await asyncio.to_thread(provider.arrived.wait, 4)
            with store.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM profile_contents WHERE profile_id = ?", (profile.id,)
                )
        finally:
            provider.release.set()
            await task
        assert provider.requests[-2:] == [("/v1", 'W/"first"'), ("/v1", None)]
        assert store.read_config(profile.id) == SAMPLE_CONFIG

    try:
        asyncio.run(scenario())
    finally:
        store.close()


@pytest.mark.parametrize(("typed", "expected"), [("", 6), ("12", 12)])
def test_import_form_honors_provider_defaults_and_exposes_links(
    provider: Provider,
    qtbot: QtBot,
    loaded: Loaded,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    expected: int,
) -> None:
    models = loaded.view_models
    monkeypatch.setattr(models.library.profiles, "_fetcher", UrlFetcher())
    url = provider.url + "/v1"
    models.shell.setPage("profiles")
    models.profiles.prefillImport(url, "")
    qtbot.waitUntil(lambda: loaded.focused_text() == url)
    field = loaded.item(lambda item: kind(item) == "AutoUpdateField" and item.isVisible())
    assert field.property("intervalText") == ""
    if typed:
        field.setProperty("intervalText", typed)
    submit = loaded.item(
        lambda item: (
            kind(item) == "AppButton" and item.property("text") == "导入" and item.isVisible()
        )
    )
    with qtbot.waitSignal(models.profiles.imported):
        loaded.click(qtbot, submit)
    p = models.library.snapshot().profiles[-1]
    assert (p.name, p.auto_update_hours) == ("测试订阅", expected)
    models.profiles.editProfile(p.id)
    qtbot.waitUntil(lambda: loaded.focused_text() == "测试订阅")
    buttons = loaded.overlay_items(lambda item: kind(item) == "AppButton" and item.isVisible())
    assert {"官网", "帮助"} <= {button.property("text") for button in buttons}
    assert (
        models.profiles.property("editDetails")["website"]
        == provider.headers["Profile-Web-Page-Url"]
    )
    assert loaded.warnings == []


@pytest.mark.parametrize("status", [200, 206])
def test_incomplete_body_does_not_replace_accepted_config_or_etag(
    provider: Provider, tmp_path: Path, status: int
) -> None:
    store = ProfileStore(tmp_path)
    library = ProfileLibrary(store, SimulatedHost(), UrlFetcher())
    try:
        (p,) = asyncio.run(library.profiles.import_url(provider.url + "/v1")).profiles
        original_tag = store.cached_etag(p.id, p.url)
        provider.status = status
        provider.headers.update(
            {"ETag": '"other"', "Content-Length": str(len(provider.body) + 100)}
        )
        with pytest.raises(ProfileError, match="download_failed"):
            asyncio.run(library.profiles.update(p.id))
        assert store.read_config(p.id) == SAMPLE_CONFIG
        assert store.cached_etag(p.id, p.url) == original_tag
    finally:
        store.close()


def test_offline_import_needs_validation_before_etag_is_reusable(
    provider: Provider, tmp_path: Path
) -> None:
    store = ProfileStore(tmp_path)
    host = SimulatedHost()
    host.core_needs_tunnel = True
    library = ProfileLibrary(store, host, UrlFetcher())
    try:
        asyncio.run(host.stop())
        (p,) = asyncio.run(library.profiles.import_url(provider.url + "/v1")).profiles
        assert p.unread
        assert store.cached_etag(p.id, p.url) is None
        asyncio.run(library.tunnel.start())
        asyncio.run(library.profiles.update(p.id))
        assert provider.requests == [("/v1", None), ("/v1", 'W/"first"')]
    finally:
        store.close()
