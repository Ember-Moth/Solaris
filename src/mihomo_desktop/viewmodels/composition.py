"""Build the ViewModel graph once, for the app, the preview and the tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from mihomo_desktop.desktop.login import LoginItem, UnavailableLoginItem
from mihomo_desktop.host.base import TunnelHost
from mihomo_desktop.models.library import ProfileLibrary
from mihomo_desktop.models.settings import SettingsStore
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.models.subscription import Fetcher
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.desktop import DesktopViewModel
from mihomo_desktop.viewmodels.overview import OverviewViewModel
from mihomo_desktop.viewmodels.profiles import ProfilesViewModel
from mihomo_desktop.viewmodels.proxies import ProxiesViewModel
from mihomo_desktop.viewmodels.settings import SettingsViewModel
from mihomo_desktop.viewmodels.shell import ShellViewModel


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
    settings: SettingsViewModel
    desktop: DesktopViewModel

    def initial_properties(self) -> dict[str, object]:
        """Main.qml's required properties."""
        return {
            "shellViewModel": self.shell,
            "overviewViewModel": self.overview,
            "proxiesViewModel": self.proxies,
            "profilesViewModel": self.profiles,
            "settingsViewModel": self.settings,
            "desktopViewModel": self.desktop,
        }

    def start(self) -> None:
        self.runner.start()
        self.shell.start()
        self.overview.start()
        self.proxies.refresh()
        self.profiles.start()
        self.settings.start()
        self.desktop.start()

    def shutdown(self) -> None:
        self.shell.stop()
        self.overview.stop()
        self.profiles.stop()
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
) -> ViewModels:
    """``clock`` dates profile updates and subscription expiry. The settings live beside the
    profiles unless given. ``tray_available`` and ``start_hidden``: see DesktopViewModel."""
    runner = AsyncRunner()
    settings_store = settings or SettingsStore(store.directory)
    library = ProfileLibrary(store, host, fetcher, clock, settings=lambda: settings_store.current)
    shell = ShellViewModel(host, runner)
    overview = OverviewViewModel(host, library, runner, settings_store)
    proxies = ProxiesViewModel(host, runner)
    profiles = ProfilesViewModel(library, runner, clock)
    settings_page = SettingsViewModel(
        settings_store, library, host, runner, login_item or UnavailableLoginItem()
    )
    desktop = DesktopViewModel(
        settings_store, tray_available=tray_available, start_hidden=start_hidden
    )

    def page_shown() -> None:
        if shell.currentPage == "proxies":
            proxies.refresh()
        elif shell.currentPage == "settings":
            settings_page.refreshInfo()
            settings_page.refreshLoginItem()

    shell.currentPageChanged.connect(page_shown)
    proxies.selectionApplied.connect(overview.refreshGroups)
    profiles.applied.connect(overview.refreshGroups)
    profiles.applied.connect(proxies.refresh)
    settings_page.changed.connect(desktop.settingsChanged)
    return ViewModels(
        runner,
        host,
        library,
        settings_store,
        shell,
        overview,
        proxies,
        profiles,
        settings_page,
        desktop,
    )
