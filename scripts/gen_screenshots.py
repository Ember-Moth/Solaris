"""Render the documentation's screenshots, docs/images/*-light.png and *-dark.png.

    uv run python scripts/gen_screenshots.py                # every shot
    uv run python scripts/gen_screenshots.py overview proxies  # only these

The real QML window, offscreen at twice the pixels, against the simulated host and the
sample profiles that scripts/preview.py runs with: nothing connects, takes over the system
or reaches the network. The window has a fixed size and Chinese text; the title band and the
settings follow macOS, as on the platform this runs on, so run it on a Mac. The tunnel is
connected and routes with the first sample profile's rules; the routing page's shots show my
plan, the default template. Run this after a change to a page that a screenshot shows, look
at the result, and commit the images (docs/development.md).
Source-tree only, like the preview: the simulated host never ships in the package.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as a script, Python puts scripts/ on the path rather than the root.
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from solaris.app import load_ui  # noqa: E402
from solaris.desktop.login import LoginItemStatus  # noqa: E402
from solaris.host.base import TAKEOVER_TUN  # noqa: E402
from solaris.i18n import Translations  # noqa: E402
from solaris.models.backup_icloud import ICloudProvider  # noqa: E402
from solaris.models.plans import default_plan  # noqa: E402
from solaris.models.routing import MODE_PLAN, MODE_PROFILE, RoutingStore  # noqa: E402
from solaris.models.store import ProfileStore  # noqa: E402
from solaris.product import DISPLAY_NAME  # noqa: E402
from solaris.viewmodels.appearance import Appearance  # noqa: E402
from solaris.viewmodels.composition import ViewModels, compose  # noqa: E402
from tests.support.appearance import SystemColorScheme  # noqa: E402
from tests.support.credentials import MemoryCredentials  # noqa: E402
from tests.support.icloud import FakeICloud  # noqa: E402
from tests.support.releases import offline_updater  # noqa: E402
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles  # noqa: E402
from tests.support.window import find, kind  # noqa: E402

OUTPUT = ROOT / "docs" / "images"
SIZE = (1280, 800)  # the window, in points: its default size (DESIGN.md)
SCALE = 2  # pixels per point
APPEARANCES = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}
RADIUS = 10.0  # the frame's corners, in points
# A hairline around the window, as the system draws one, in each appearance.
EDGE = {"light": QColor(0, 0, 0, 40), "dark": QColor(255, 255, 255, 40)}
SETTLE_MS = 2500  # a page's transition, and the two polls that a rate takes
WARM_UP_POLLS = 5  # of ten seconds' traffic each: the chart shows forty
TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Shot:
    page: str  # the sidebar's page
    # Brings the page to the state the shot shows, given its item.
    prepare: Callable[[QQuickItem], None] | None = None
    # What routes: the profile's own rules, whose groups the simulated core lists, or my plan.
    mode: str = MODE_PROFILE


def _tab(name: str) -> Callable[[QQuickItem], None]:
    """Show the routing page's tab ``name``."""

    def prepare(page: QQuickItem) -> None:
        page.setProperty("tab", name)

    return prepare


SHOTS = {
    "overview": Shot("overview"),
    "proxies": Shot("proxies"),
    "profiles": Shot("profiles"),
    "connections": Shot("connections"),
    "settings": Shot("settings"),
    "routing": Shot("routing", _tab("rules"), MODE_PLAN),
    "routing-groups": Shot("routing", _tab("groups"), MODE_PLAN),
}
# The pages' items in Main.qml's StackLayout, by the sidebar's page.
PAGE_TYPES = {
    "overview": "OverviewPage",
    "proxies": "ProxiesPage",
    "profiles": "ProfilesPage",
    "routing": "RoutingPage",
    "connections": "ConnectionsPage",
    "logs": "LogsPage",
    "settings": "SettingsPage",
}


class WarpClock:
    """The time, after the jumps ``skip`` makes: the simulated host polls on it, so its
    traffic chart fills in a few seconds rather than in its forty."""

    def __init__(self) -> None:
        self._offset = 0.0

    def __call__(self) -> float:
        return time.time() + self._offset

    def skip(self, seconds: float) -> None:
        self._offset += seconds


class InstalledLoginItem:
    """Opening at login as the installed app offers it, switched off; nothing registers."""

    available = True

    def status(self) -> LoginItemStatus:
        return LoginItemStatus(enabled=False)

    def set_enabled(self, enabled: bool) -> LoginItemStatus:
        return LoginItemStatus(enabled=False)

    def open_settings(self) -> None:
        pass


def wait_until(test: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + TIMEOUT_S
    while not test():
        if time.monotonic() > deadline:
            raise TimeoutError(f"gave up waiting for {what}")
        QTest.qWait(50)


def framed(image: QImage, ratio: float, appearance: str) -> QImage:
    """The window with rounded corners and a hairline edge, transparent outside them;
    ``ratio`` is the image's pixels per point."""
    result = QImage(image.size(), QImage.Format.Format_ARGB32)
    result.fill(Qt.GlobalColor.transparent)
    bounds = QRectF(0, 0, image.width(), image.height())
    radius = RADIUS * ratio
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(bounds, radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, image)
    painter.setClipping(False)
    half = ratio / 2
    edge = QPainterPath()
    edge.addRoundedRect(bounds.adjusted(half, half, -half, -half), radius - half, radius - half)
    painter.setPen(QPen(EDGE[appearance], ratio))
    painter.drawPath(edge)
    painter.end()
    return result


def _show(window: QQuickWindow, view_models: ViewModels, shot: Shot) -> None:
    """Route as ``shot`` says, open its page and bring the page to its state."""
    routing, shell = view_models.routing, view_models.shell
    routing.setMode(shot.mode)
    wait_until(
        lambda: routing.property("mode") == shot.mode and not routing.property("busy"),
        f"routing with {shot.mode}",
    )
    shell.setPage(shot.page)
    wait_until(lambda: shell.property("currentPage") == shot.page, f"the page {shot.page}")
    if shot.prepare is not None:
        page = find(window.contentItem(), lambda item: kind(item) == PAGE_TYPES[shot.page])
        assert page is not None
        shot.prepare(page)
    QTest.qWait(SETTLE_MS)


def _load(
    store: ProfileStore, appearance: str, clock: WarpClock
) -> tuple[ViewModels, QQmlApplicationEngine]:
    seed_profiles(store, time.time())
    # My plan is the default template, there to switch to.
    routing = RoutingStore(store.database)
    plan = default_plan()
    routing.put_plan(plan)
    routing.update_state(plan_id=plan.id)
    view_models = compose(
        SimulatedHost(clock=clock, takeover=TAKEOVER_TUN),
        store,
        SimulatedFetcher(),
        appearance=Appearance("system", hints=SystemColorScheme(APPEARANCES[appearance])),
        login_item=InstalledLoginItem(),
        updater=offline_updater(store.directory),
        credentials=MemoryCredentials(),
        icloud=ICloudProvider(FakeICloud()),
    )
    engine = QQmlApplicationEngine()
    if not load_ui(engine, view_models):
        raise RuntimeError("the window did not load")
    return view_models, engine


def capture(output: Path, appearance: str, names: Iterable[str]) -> list[Path]:
    """Save the shots ``names`` in ``appearance`` to ``output``; returns the files."""
    app = QCoreApplication.instance()
    assert app is not None, "needs a QGuiApplication"
    translations = Translations(app)
    translations.apply("zh")
    clock = WarpClock()
    saved = []
    with tempfile.TemporaryDirectory(prefix="solaris-screenshots-") as directory:
        store = ProfileStore(Path(directory))
        view_models, engine = _load(store, appearance, clock)
        try:
            (window,) = engine.rootObjects()
            assert isinstance(window, QQuickWindow)
            window.resize(*SIZE)
            view_models.start()
            overview = view_models.overview
            wait_until(lambda: overview.property("connected"), "the tunnel")
            for _ in range(WARM_UP_POLLS):
                clock.skip(10)
                QTest.qWait(1100)
            QTest.qWait(2000)  # the connections' rates over real seconds again
            for name in [name for name in SHOTS if name in names]:
                _show(window, view_models, SHOTS[name])
                path = output / f"{name}-{appearance}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                # Quality 0 is PNG's strongest compression; the pixels stay the same.
                image = framed(window.grabWindow(), window.devicePixelRatio(), appearance)
                if not image.save(str(path), quality=0):  # PNG, by the suffix
                    raise OSError(f"cannot write {path}")
                saved.append(path)
        finally:
            view_models.shutdown()
            for root in engine.rootObjects():
                root.deleteLater()
            del engine
            QTest.qWait(0)
            store.close()
            translations.remove()
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the documentation's screenshots")
    parser.add_argument(
        "shots", nargs="*", metavar="shot", help=f"default: all of {', '.join(SHOTS)}"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if unknown := set(args.shots) - SHOTS.keys():
        parser.error(f"no such shot: {', '.join(sorted(unknown))}")
    # Before Qt starts: headless, and twice the pixels of a point.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(SCALE)
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName(DISPLAY_NAME)
    for appearance in APPEARANCES:
        for path in capture(args.output, appearance, args.shots or SHOTS):
            print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


if __name__ == "__main__":
    main()
