"""Delete/reimport through the real UI models, HTTP downloader and mihomo service."""

import pytest
from PySide6.QtCore import QPointF, Qt

from solaris.models.routing import Plan
from solaris.models.store import ProfileStore
from tests.e2e.app import App, nodes_profile
from tests.e2e.servers import WebServer
from tests.support.window import find_all, kind

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("keep_plan", [False, True])
def test_rules_recover_unless_the_user_confirms_the_plan(app: App, keep_plan: bool) -> None:
    vm = app.view_models
    # A local-only fallback avoids downloading GEO data or running external health checks.
    store = ProfileStore(app.data)
    try:
        store.write_override(None, "dns: null\n")
    finally:
        store.close()
    done: list[bool] = []

    async def load_plan() -> bool:
        # Public creation validates against the real core; source rules still decide mode.
        await vm.library.plans.add_plan(Plan("fallback", "Fallback", "REJECT"), use=False)
        return True

    failures: list[BaseException] = []
    vm.runner.submit(load_plan(), done.append, failures.append)
    app.until(lambda: done or failures)
    assert not failures, failures
    with WebServer("subscription") as server:
        server.subscription = nodes_profile(app.network).encode()
        app.import_url(server.url("/sub"), "A")
        app.connect()
        assert app.curl(app.network.proxied.url("/")) == "proxied"
        p = app.rows(vm.profiles)[0]
        vm.profiles.requestRemove(p.id)
        app.until(lambda: bool(app.prop(vm.profiles, "removalTitle")))
        app.until(
            lambda: bool(
                find_all(
                    app.window.contentItem(),
                    lambda i: kind(i) == "AppButton" and i.property("text") == "删除",
                )
            )
        )
        button = find_all(
            app.window.contentItem(),
            lambda i: kind(i) == "AppButton" and i.property("text") == "删除",
        )[0]
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        app.qtbot.mouseClick(app.window, Qt.MouseButton.LeftButton, pos=point)
        app.until(lambda: not app.rows(vm.profiles))
        app.until(lambda: app.prop(vm.routing, "mode") == "plan")
        assert vm.library.routing_snapshot().state.resume_profile
        if keep_plan:
            vm.shell.setPage("routing")
            # A click on the already selected segment must reach the ViewModel.
            app.until(
                lambda: bool(find_all(app.window.contentItem(), lambda i: kind(i) == "RoutingPage"))
            )
            # Wait for the page transition before mapping a point into the window.
            app.qtbot.wait(300)
            page = find_all(app.window.contentItem(), lambda i: kind(i) == "RoutingPage")[0]
            controls = find_all(page, lambda i: kind(i) == "SegmentedControl")
            selected = find_all(controls[0], lambda i: i.property("selected") is True)[0]
            point = selected.mapToScene(
                QPointF(selected.width() / 2, selected.height() / 2)
            ).toPoint()
            app.qtbot.mouseClick(app.window, Qt.MouseButton.LeftButton, pos=point)
            app.until(lambda: not vm.library.routing_snapshot().state.resume_profile)
        server.subscription = nodes_profile(app.network).encode()
        app.import_url(server.url("/sub"), "B")
        app.until(lambda: app.prop(vm.routing, "mode") == ("plan" if keep_plan else "profile"))
        assert not vm.library.routing_snapshot().state.resume_profile
        result = app.curl(app.network.proxied.url("/"))
        assert (result == "proxied") is (not keep_plan)
        assert not app.warnings
