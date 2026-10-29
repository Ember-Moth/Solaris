"""Build the ViewModel graph once, for the app, the preview and the tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from mihomo_desktop.host.base import TunnelHost
from mihomo_desktop.models.library import ProfileLibrary
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.models.subscription import Fetcher
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.connections import ConnectionsViewModel
from mihomo_desktop.viewmodels.logs import LogsViewModel
from mihomo_desktop.viewmodels.overview import OverviewViewModel
from mihomo_desktop.viewmodels.profiles import ProfilesViewModel
from mihomo_desktop.viewmodels.proxies import ProxiesViewModel
from mihomo_desktop.viewmodels.shell import ShellViewModel


@dataclass
class ViewModels:
    runner: AsyncRunner
    host: TunnelHost
    library: ProfileLibrary
    shell: ShellViewModel
    overview: OverviewViewModel
    proxies: ProxiesViewModel
    profiles: ProfilesViewModel
    connections: ConnectionsViewModel
    logs: LogsViewModel

    def initial_properties(self) -> dict[str, object]:
        """Main.qml's required properties."""
        return {
            "shellViewModel": self.shell,
            "overviewViewModel": self.overview,
            "proxiesViewModel": self.proxies,
            "profilesViewModel": self.profiles,
            "connectionsViewModel": self.connections,
            "logsViewModel": self.logs,
        }

    def start(self) -> None:
        self.runner.start()
        self.shell.start()
        self.overview.start()
        self.proxies.refresh()
        self.profiles.start()
        self.logs.start()

    def shutdown(self) -> None:
        self.shell.stop()
        self.overview.stop()
        self.profiles.stop()
        self.connections.stop()
        self.logs.stop()
        self.runner.shutdown(finalize=self.host.close)


def compose(
    host: TunnelHost,
    store: ProfileStore,
    fetcher: Fetcher,
    clock: Callable[[], float] = time.time,
) -> ViewModels:
    """``clock`` dates profile updates and subscription expiry."""
    runner = AsyncRunner()
    library = ProfileLibrary(store, host, fetcher, clock)
    shell = ShellViewModel(host, runner)
    overview = OverviewViewModel(host, library, runner)
    proxies = ProxiesViewModel(host, runner)
    profiles = ProfilesViewModel(library, runner, clock)
    connections = ConnectionsViewModel(host, runner)
    logs = LogsViewModel(host, runner)

    def page_shown() -> None:
        if shell.currentPage == "proxies":
            proxies.refresh()
        connections.setActive(shell.currentPage == "connections")

    shell.currentPageChanged.connect(page_shown)
    proxies.selectionApplied.connect(overview.refreshGroups)
    profiles.applied.connect(overview.refreshGroups)
    profiles.applied.connect(proxies.refresh)
    return ViewModels(runner, host, library, shell, overview, proxies, profiles, connections, logs)
