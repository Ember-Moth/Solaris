"""Install links (docs/release.md §7): parsing both schemes, the ways a link arrives, and the
import form it fills in."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QFileOpenEvent
from pytestqt.qtbot import QtBot

from solaris.desktop import links
from solaris.desktop.instance import ACTIVATE, SingleInstance, message
from solaris.desktop.links import InstallLink, LinkReceiver, from_arguments, parse
from solaris.models.store import ProfileStore
from solaris.viewmodels.composition import ViewModels, compose
from tests.support.releases import offline_updater
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles
from tests.support.window import Loaded, find, kind

SUBSCRIPTION = "https://sub.example.com/api/v1/client/subscribe?token=abc&flag=clash"
ENCODED = quote(SUBSCRIPTION, safe="")


@pytest.mark.parametrize(
    ("link", "parsed"),
    [
        (f"solaris://install-config?url={ENCODED}&name=%E6%9C%BA%E5%9C%BA", ("机场",)),
        (f"clash://install-config?url={ENCODED}", ("",)),
        (f"CLASH://Install-Config/?url={ENCODED}&name=Hi", ("Hi",)),
        (f"solaris://install-config?name=+%20Spaced%20+&url={ENCODED}", ("Spaced",)),
        (f"solaris://install-config?url={ENCODED}&name=a%0Ab%09c", ("abc",)),
        (f"solaris://install-config?url={ENCODED}&name={'x' * 150}", ("x" * 100,)),
    ],
)
def test_parse(link: str, parsed: tuple[str]) -> None:
    assert parse(link) == InstallLink(SUBSCRIPTION, parsed[0])


@pytest.mark.parametrize(
    "link",
    [
        f"https://install-config?url={ENCODED}",  # another scheme
        f"solaris://import?url={ENCODED}",  # another action
        f"solaris://install-config/more?url={ENCODED}",
        "solaris://install-config?name=only",  # no url
        f"solaris://install-config?url={ENCODED}&url={ENCODED}",  # two
        "solaris://install-config?url=file%3A%2F%2F%2Fetc%2Fpasswd",
        "solaris://install-config?url=javascript%3Aalert(1)",
        "clash://install-config?url=ftp%3A%2F%2Fexample.com%2Fsub",
        f"solaris://install-config?url={ENCODED}&name={'x' * 9000}",  # too long
        "solaris:",
        "",
    ],
)
def test_parse_refuses(link: str) -> None:
    assert parse(link) is None


def test_unencoded_ampersands_cut_the_link() -> None:
    # As in other clients: the provider must encode the subscription link.
    parsed = parse(f"clash://install-config?url={SUBSCRIPTION}")
    assert parsed is not None
    assert parsed.url == "https://sub.example.com/api/v1/client/subscribe?token=abc"


def test_link_from_the_command_line() -> None:
    link = f"solaris://install-config?url={ENCODED}"
    assert from_arguments(["--background", link, "clash://other"]) == link
    assert from_arguments(["--background"]) is None
    assert from_arguments(["solaris" + "x" * 9000 + ":"]) is None


def test_the_message_a_later_ui_sends() -> None:
    assert message(None) == ACTIVATE
    assert message("solaris://install-config?url=x") == b"open solaris://install-config?url=x\n"
    assert message("solaris://a\r\nactivate") == b"open solaris://aactivate\n"


# A second UI started with a link, in a process of its own: see test_desktop.py.
SECOND_UI = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from solaris.desktop.instance import SingleInstance
app = QCoreApplication(sys.argv)
sys.exit(1 if SingleInstance(Path(sys.argv[1])).claim(sys.argv[2]) else 0)
"""


def test_a_second_ui_hands_over_its_link(qtbot: QtBot, tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    assert first.claim()
    link = f"solaris://install-config?url={ENCODED}&name=%E6%9C%BA%E5%9C%BA"
    second = subprocess.Popen([sys.executable, "-c", SECOND_UI, str(tmp_path), link])
    try:
        with qtbot.waitSignal(first.received, timeout=8000) as received:
            pass
        assert received.args == [link]
        assert second.wait(timeout=10) == 0
    finally:
        second.kill()
        second.wait()
        first.release()


def test_the_system_opens_a_link(qtbot: QtBot) -> None:
    app = QCoreApplication.instance()
    assert app is not None
    receiver = LinkReceiver(app)
    seen: list[str] = []
    receiver.received.connect(seen.append)
    link = f"solaris://install-config?url={ENCODED}"
    # Before the UI is ready the link waits, as on a launch that a link started.
    QCoreApplication.sendEvent(app, QFileOpenEvent(QUrl(link)))
    QCoreApplication.sendEvent(app, QFileOpenEvent(QUrl.fromLocalFile("/tmp/a.yaml")))
    assert seen == []
    receiver.ready()
    assert len(seen) == 1
    assert parse(seen[0]) == InstallLink(SUBSCRIPTION, "")
    receiver.deliver("clash://install-config?url=x")
    assert seen[1] == "clash://install-config?url=x"
    app.removeEventFilter(receiver)


@pytest.fixture
def built(qtbot: QtBot, tmp_path: Path) -> Iterator[ViewModels]:
    store = ProfileStore(tmp_path)
    seed_profiles(store, time.time())
    view_models = compose(
        SimulatedHost(), store, SimulatedFetcher(), updater=offline_updater(tmp_path)
    )
    view_models.runner.start()
    yield view_models
    view_models.shutdown()


def test_a_link_fills_in_the_import_form(qtbot: QtBot, built: ViewModels) -> None:
    shown: list[bool] = []
    built.desktop.showWindowRequested.connect(lambda: shown.append(True))
    with qtbot.waitSignal(built.profiles.importPrefilled):
        built.open_link(f"solaris://install-config?url={ENCODED}&name=%E6%9C%BA%E5%9C%BA")
    assert shown == [True]
    assert built.shell.currentPage == "profiles"
    assert built.profiles.property("prefillUrl") == SUBSCRIPTION
    assert built.profiles.property("prefillName") == "机场"
    assert not built.profiles.property("hasError")


def test_an_unknown_link_says_so(qtbot: QtBot, built: ViewModels) -> None:
    built.open_link("solaris://something-else")
    assert built.shell.currentPage == "profiles"
    assert built.profiles.property("summary") == "无法识别的导入链接"
    assert built.profiles.property("hasError")


def test_the_form_opens_filled_in(qtbot: QtBot, loaded: Loaded) -> None:
    view_models = loaded.view_models
    view_models.open_link(f"clash://install-config?url={ENCODED}&name=Mine")
    root = loaded.window.contentItem()

    def field(text: str) -> object:
        return find(root, lambda i: kind(i) == "AppTextField" and i.property("text") == text)

    qtbot.waitUntil(lambda: field(SUBSCRIPTION) is not None)
    assert field("Mine") is not None
    assert loaded.focused_text() == SUBSCRIPTION
    # Nothing was imported: the user checks the link and presses 导入.
    assert len(view_models.library.snapshot().profiles) == 3


def test_links_module_names_both_schemes() -> None:
    assert links.SCHEMES == ("solaris", "clash")
