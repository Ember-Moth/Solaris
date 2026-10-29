import pytest
from PySide6.QtCore import QLocale

from mihomo_desktop.i18n import LANG_ENV, resolve_language, ui_locale
from scripts.gen_i18n import stale_binaries, stale_catalogs, unfinished_messages


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
        ("", {}, ["zh-Hant-TW"], "zh"),
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


def test_dates_keep_the_system_conventions_of_the_same_language() -> None:
    assert ui_locale("en", QLocale("en_GB")).name() == "en_GB"
    assert ui_locale("en", QLocale("de_DE")).name() == "en_US"
    assert ui_locale("zh", QLocale("zh_TW")).name() == "zh_TW"
    assert ui_locale("zh", QLocale("en_US")).name() == "zh_CN"
