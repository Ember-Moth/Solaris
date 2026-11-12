import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QLocale, QObject
from pytestqt.qtbot import QtBot

from scripts.gen_i18n import stale_binaries, stale_catalogs, unfinished_messages
from solaris.host.base import HostError
from solaris.i18n import LANG_ENV, resolve_language, system_ui_languages, ui_locale
from solaris.models import format as fmt
from solaris.models.profiles import ProfileError
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from solaris.viewmodels.feedback import failure_text
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

NOW = datetime(2026, 10, 29, 12).timestamp()


def test_catalogs_cover_the_sources() -> None:
    assert stale_catalogs() == []


def test_english_is_finished() -> None:
    assert unfinished_messages() == []


def test_compiled_catalogs_match() -> None:
    assert stale_binaries() == []


@pytest.mark.parametrize(
    ("preference", "environ", "system", "language"),
    [
        ("", {}, ["zh-Hans-CN", "en-US"], "zh"),
        ("", {}, ["zh-Hans-US", "en-US"], "zh"),
        ("", {}, ["zh-Hans"], "zh"),
        ("", {}, ["zh-CN"], "zh"),
        ("", {}, ["zh"], "zh"),
        ("", {}, ["zh-Hant-TW"], "zh"),
        ("", {}, ["en-CN", "zh-Hans-CN"], "en"),
        ("", {}, ["en-US", "zh-Hans-US"], "en"),
        ("", {}, ["ja-JP", "zh-Hans-CN"], "en"),
        ("", {}, [], "en"),
        ("", {LANG_ENV: "zh"}, ["en-US"], "zh"),
        ("", {LANG_ENV: " EN "}, ["zh-Hans-CN"], "en"),
        ("", {LANG_ENV: "fr"}, ["zh-Hans-CN"], "zh"),
        ("system", {}, ["zh-Hans-CN"], "zh"),
        ("en", {LANG_ENV: "zh"}, ["zh-Hans-CN"], "en"),
    ],
)
def test_language_choice(
    preference: str, environ: dict[str, str], system: list[str], language: str
) -> None:
    assert resolve_language(preference, environ, system) == language


LANGUAGE_PROBE = """
from solaris.i18n import resolve_language, ui_locale
language = resolve_language("", {})
print(language, ui_locale(language).name())
"""


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreFoundation is on macOS")
@pytest.mark.parametrize(
    ("lang", "preferences", "shown"),
    [
        # Opened from Finder: no LANG, so PySide6's QLocale.system() is C.
        (None, ["-AppleLanguages", "(zh-Hans-CN, en-CN)", "-AppleLocale", "zh_CN"], "zh zh_CN"),
        ("en_US.UTF-8", ["-AppleLanguages", "(zh-Hans-CN)", "-AppleLocale", "zh_CN"], "zh zh_CN"),
        ("zh_CN.UTF-8", ["-AppleLanguages", "(en-GB)", "-AppleLocale", "en_GB"], "en en_GB"),
    ],
)
def test_macos_follows_the_system_not_the_posix_locale(
    lang: str | None, preferences: list[str], shown: str
) -> None:
    environ = {k: v for k, v in os.environ.items() if not k.startswith(("LANG", "LC_"))}
    if lang:
        environ["LANG"] = lang
    # The arguments stand in for the system's settings, as for any app on macOS.
    command = [sys.executable, "-c", LANGUAGE_PROBE, *preferences]
    result = subprocess.run(command, env=environ, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == shown


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreFoundation is on macOS")
def test_macos_system_languages_are_tags() -> None:
    languages = system_ui_languages()
    assert languages
    assert "C" not in languages


def test_dates_keep_the_system_conventions_of_the_same_language() -> None:
    assert ui_locale("en", QLocale("en_GB")).name() == "en_GB"
    assert ui_locale("en", QLocale("de_DE")).name() == "en_US"
    assert ui_locale("zh", QLocale("zh_TW")).name() == "zh_TW"
    assert ui_locale("zh", QLocale("en_US")).name() == "zh_CN"


@pytest.mark.usefixtures("english")
def test_english_formats() -> None:
    assert fmt.ago(NOW - 30, NOW) == "just now"
    assert fmt.ago(NOW - 60, NOW) == "1 minute ago"
    assert fmt.ago(NOW - 5 * 60, NOW) == "5 minutes ago"
    assert fmt.ago(NOW - 3600, NOW) == "1 hour ago"
    assert fmt.ago(NOW - 3 * 86400, NOW) == "3 days ago"
    assert fmt.ago(datetime(2026, 10, 20, 9).timestamp(), NOW) == "Oct 20"
    assert fmt.ago(datetime(2025, 10, 20, 9).timestamp(), NOW) == "Oct 20, 2025"
    assert fmt.date(datetime(2026, 12, 31).timestamp()) == "Dec 31, 2026"
    assert fmt.latency(0) == ("Timeout", "timeout")


@pytest.mark.usefixtures("english")
def test_english_plurals() -> None:
    def groups(count: int) -> str:
        return QCoreApplication.translate("ProxiesViewModel", "%n 个代理组", None, count)

    assert groups(1) == "1 proxy group"
    assert groups(3) == "3 proxy groups"


def prop(view_model: QObject, name: str) -> Any:
    return view_model.property(name)


def rows(view_model: QObject, name: str = "model") -> list[Any]:
    return list(prop(view_model, name).rows)


@pytest.fixture
def view_models(english: None, qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, NOW)
    built = compose(SimulatedHost(), store, SimulatedFetcher(), lambda: NOW)
    built.start()
    yield built
    built.shutdown()


def test_english_view_models(qtbot: QtBot, view_models: ViewModels) -> None:
    shell, overview, proxies = view_models.shell, view_models.overview, view_models.proxies
    qtbot.waitUntil(lambda: prop(shell, "serviceAvailable"))
    assert prop(shell, "serviceText") == "Service running"
    assert prop(shell, "serviceDetail") == "v1.19.33\nProtocol v1"

    qtbot.waitUntil(lambda: prop(overview, "connected"))
    assert [page.label for page in prop(shell, "pages").rows][:4] == [
        "Overview",
        "Proxies",
        "Subscriptions",
        "Routing",
    ]
    assert prop(overview, "statusTitle") == "Connected"
    assert prop(overview, "statusDetail").startswith("TUN and system proxy · running for ")
    assert prop(overview, "connectLabel") == "Disconnect"
    assert [mode["label"] for mode in prop(overview, "modeOptions")] == ["Rule", "Global", "Direct"]
    assert prop(overview, "connectionsLink").startswith("All ")

    qtbot.waitUntil(lambda: prop(proxies, "currentGroup") != "")
    assert prop(proxies, "summary").endswith(" proxy groups")
    assert [s["label"] for s in prop(proxies, "sortOptions")] == ["Default", "Name", "Latency"]
    assert prop(proxies, "currentHint") == "Click to select"


def test_english_profiles(qtbot: QtBot, view_models: ViewModels) -> None:
    profiles, overview = view_models.profiles, view_models.overview
    assert prop(profiles, "summary") == "3 subscriptions"
    a, b, c = rows(profiles)
    assert a.source == "Subscription · sub.example.com · 9 proxies"
    assert (a.expiry, a.updated, a.refreshLabel) == (
        "Dec 31, 2026",
        "2 hours ago",
        "Update",
    )
    assert b.updated == "3 days ago"
    assert (c.source, c.refreshLabel) == ("Local file · config.yaml · 9 proxies", "Reload")
    assert prop(profiles, "activeDetail") == "sub.example.com · Updated 2 hours ago"
    assert prop(profiles, "activeExpiry") == "Expires Dec 31, 2026"
    assert prop(profiles, "model").rows[0].autoUpdateLabel == "Update every 12 hours"
    assert prop(profiles, "globalOverrideDetail") == "Applies to both routing modes · Not set"

    # The two sentences of a removal's consequences are joined with a space.
    qtbot.waitUntil(lambda: prop(overview, "connected"))
    with qtbot.waitSignal(profiles.removalRequested):
        profiles.requestRemove("sample-a")
    assert prop(profiles, "removalTitle") == "Delete “示例订阅 A”?"
    assert prop(profiles, "removalText") == (
        "Its rules are in use; the rules of “示例订阅 B” take over. "
        "The subscription will be deleted from this computer. This can’t be undone."  # noqa: RUF001
    )


@pytest.mark.usefixtures("english")
def test_english_failures() -> None:
    assert failure_text(ProfileError("http_error", "HTTP 404")) == "Download failed: HTTP 404"
    timeout = failure_text(HostError("timeout", "no reply"))
    assert timeout == "The service isn’t responding"  # noqa: RUF001
    assert failure_text(HostError("brand_new", "it broke")) == "Operation failed: it broke"
    assert failure_text(RuntimeError("boom")) == "Operation failed: boom"
