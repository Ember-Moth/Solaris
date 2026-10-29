"""Build the ViewModel graph once, for the app, the preview and the tests."""

from __future__ import annotations

from dataclasses import dataclass

from mihomo_desktop.host.base import TunnelHost
from mihomo_desktop.models.profiles import ProfileSource
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.overview import OverviewViewModel
from mihomo_desktop.viewmodels.proxies import ProxiesViewModel
from mihomo_desktop.viewmodels.shell import ShellViewModel


@dataclass
class ViewModels:
    runner: AsyncRunner
    host: TunnelHost
    shell: ShellViewModel
    overview: OverviewViewModel
    proxies: ProxiesViewModel

    def initial_properties(self) -> dict[str, object]:
        """Main.qml's required properties."""
        return {
            "shellViewModel": self.shell,
            "overviewViewModel": self.overview,
            "proxiesViewModel": self.proxies,
        }

    def start(self) -> None:
        self.runner.start()
        self.shell.start()
        self.overview.start()
        self.proxies.refresh()

    def shutdown(self) -> None:
        self.shell.stop()
        self.overview.stop()
        self.runner.shutdown(finalize=self.host.close)


def compose(host: TunnelHost, profiles: ProfileSource) -> ViewModels:
    runner = AsyncRunner()
    shell = ShellViewModel(host, profiles, runner)
    overview = OverviewViewModel(host, profiles, runner)
    proxies = ProxiesViewModel(host, runner)

    def page_shown() -> None:
        if shell.currentPage == "proxies":
            proxies.refresh()

    shell.currentPageChanged.connect(page_shown)
    proxies.selectionApplied.connect(overview.refreshGroups)
    return ViewModels(runner, host, shell, overview, proxies)
