"""Build the ViewModel graph once, for the app, the preview and the tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from solaris.desktop.login import LoginItem, UnavailableLoginItem
from solaris.host.base import TunnelHost
from solaris.models.library import ProfileLibrary
from solaris.models.settings import DatabaseSettingsStore, Settings, SettingsStore
from solaris.models.store import ProfileStore
from solaris.models.subscription import Fetcher
from solaris.viewmodels.appearance import Appearance
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.connections import ConnectionsViewModel
from solaris.viewmodels.desktop import DesktopViewModel
from solaris.viewmodels.logs import LogsViewModel
from solaris.viewmodels.overview import OverviewViewModel
from solaris.viewmodels.profiles import ProfilesViewModel
from solaris.viewmodels.proxies import ProxiesViewModel
from solaris.viewmodels.routing import RoutingViewModel
from solaris.viewmodels.settings import SettingsViewModel
from solaris.viewmodels.shell import ShellViewModel


@dataclass
class ViewModels:
    runner: AsyncRunner
    host: TunnelHost
    library: ProfileLibrary
    settings_store: SettingsStore
    shell: ShellViewModel
    overview: OverviewViewModel
    proxies: ProxiesViewModel
    profiles: ProfilesViewModel
    routing: RoutingViewModel
    connections: ConnectionsViewModel
    logs: LogsViewModel
    settings: SettingsViewModel
    desktop: DesktopViewModel
    appearance: Appearance

    def initial_properties(self) -> dict[str, object]:
        """Main.qml's required properties."""
        return {
            "shellViewModel": self.shell,
            "overviewViewModel": self.overview,
            "proxiesViewModel": self.proxies,
            "profilesViewModel": self.profiles,
            "routingViewModel": self.routing,
            "connectionsViewModel": self.connections,
            "logsViewModel": self.logs,
            "appearance": self.appearance,
            "settingsViewModel": self.settings,
            "desktopViewModel": self.desktop,
        }

    def start(self) -> None:
        self.runner.start()
        self.shell.start()
        self.overview.start()
        self.proxies.refresh()
        self.profiles.start()
        self.routing.refresh()
        self.logs.start()
        self.settings.start()
        self.desktop.start()

    def retranslate(self) -> None:
        """Re-read every text the ViewModels make, after the UI language changed."""
        self.shell.retranslate()
        self.overview.retranslate()
        self.proxies.retranslate()
        self.profiles.retranslate()
        self.routing.retranslate()
        self.connections.retranslate()
        self.logs.retranslate()
        self.settings.retranslate()

    def shutdown(self) -> None:
        self.shell.stop()
        self.overview.stop()
        self.profiles.stop()
        self.connections.stop()
        self.logs.stop()
        self.desktop.stop()
        self.runner.shutdown(finalize=self.host.close)


def compose(
    host: TunnelHost,
    store: ProfileStore,
    fetcher: Fetcher,
    clock: Callable[[], float] = time.time,
    *,
    settings: SettingsStore | None = None,
    login_item: LoginItem | None = None,
    tray_available: bool = False,
    start_hidden: bool = False,
    appearance: Appearance | None = None,
) -> ViewModels:
    """``clock`` dates profile updates and subscription expiry. The settings live in the
    profiles' database unless given. ``tray_available`` and ``start_hidden``: see DesktopViewModel.
    ``appearance`` defaults to the stored choice; the settings page changes it from then on."""
    runner = AsyncRunner()
    settings_store = settings or DatabaseSettingsStore(store.database)
    library = ProfileLibrary(store, host, fetcher, clock, settings=lambda: settings_store.current)
    shell = ShellViewModel(host, runner)
    overview = OverviewViewModel(host, library, runner, settings_store)
    proxies = ProxiesViewModel(host, runner)
    profiles = ProfilesViewModel(library, runner, clock)
    routing = RoutingViewModel(library, runner)
    connections = ConnectionsViewModel(host, runner)
    logs = LogsViewModel(host, runner)
    settings_page = SettingsViewModel(
        settings_store, library, host, runner, login_item or UnavailableLoginItem()
    )
    desktop = DesktopViewModel(
        settings_store, tray_available=tray_available, start_hidden=start_hidden
    )

    def page_shown() -> None:
        if shell.currentPage == "proxies":
            proxies.refresh()
        elif shell.currentPage == "routing":
            routing.refresh()
        elif shell.currentPage == "settings":
            settings_page.refreshInfo()
            settings_page.refreshLoginItem()
        connections.setActive(shell.currentPage == "connections")

    shell.currentPageChanged.connect(page_shown)
    proxies.selectionApplied.connect(overview.refreshGroups)
    profiles.applied.connect(overview.refreshGroups)
    profiles.applied.connect(proxies.refresh)
    # One page's change of the pool or the routing shows on the other (routing.md §4.2).
    profiles.routingChanged.connect(routing.reload)
    profiles.routingRequested.connect(lambda: shell.setPage("routing"))
    routing.routingChanged.connect(profiles.reload)
    routing.applied.connect(overview.refreshGroups)
    routing.applied.connect(proxies.refresh)

    def routing_shown() -> None:
        proxies.set_pool(*routing.pool_sources())
        connections.set_entry_names(routing.entry_names())

    routing.changed.connect(routing_shown)
    routing_shown()
    appearance = appearance or Appearance(settings_store.current.appearance)

    def appearance_stored(before: Settings, after: Settings) -> None:
        if after.appearance != before.appearance:
            appearance.setMode(after.appearance)

    settings_store.subscribe(appearance_stored)
    return ViewModels(
        runner,
        host,
        library,
        settings_store,
        shell,
        overview,
        proxies,
        profiles,
        routing,
        connections,
        logs,
        settings_page,
        desktop,
        appearance,
    )
