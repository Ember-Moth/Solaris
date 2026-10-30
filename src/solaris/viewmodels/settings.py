"""Settings ViewModel: the mixed port, LAN access and TUN stack, the UI language and
appearance, what the desktop does (closing the window, opening at login) and what is installed.

The page's TUN and system proxy switches come from OverviewViewModel, which knows the
running tunnel's state (D26).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement, QmlUncreatable

from solaris import __version__
from solaris.desktop.login import LoginItem, LoginItemStatus
from solaris.host.base import HostInfo, TunnelHost
from solaris.i18n import QT_TRANSLATE_NOOP
from solaris.models.library import ProfileLibrary
from solaris.models.settings import (
    ALL_NETWORKS_BREAKS,
    APPEARANCES,
    CLOSE_ACTIONS,
    LANGUAGES,
    MAX_MIXED_PORT,
    MIN_MIXED_PORT,
    TUN_STACK_CHOICES,
    Settings,
    SettingsError,
    SettingsStore,
    valid_port,
)
from solaris.viewmodels.common.async_runner import AsyncRunner
from solaris.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "Solaris"
QML_IMPORT_MAJOR_VERSION = 1

LICENSE = "GPL-3.0-only"
PROJECT_URL = "https://github.com/Ember-Moth/Solaris"

LANGUAGE_LABELS = {
    "system": QT_TRANSLATE_NOOP("SettingsViewModel", "跟随系统"),
    # Each language is named in itself, so it can be found from either UI.
    "zh": "简体中文",
    "en": "English",
}
APPEARANCE_LABELS = {
    "system": QT_TRANSLATE_NOOP("SettingsViewModel", "跟随系统"),
    "light": QT_TRANSLATE_NOOP("SettingsViewModel", "浅色"),
    "dark": QT_TRANSLATE_NOOP("SettingsViewModel", "深色"),
}
CLOSE_ACTION_LABELS = {
    # macOS calls the tray the menu bar.
    "tray": QT_TRANSLATE_NOOP("SettingsViewModel", "留在菜单栏")
    if sys.platform == "darwin"
    else QT_TRANSLATE_NOOP("SettingsViewModel", "留在托盘"),
    "quit": QT_TRANSLATE_NOOP("SettingsViewModel", "退出界面"),
}
TUN_STACK_LABELS = {
    "profile": QT_TRANSLATE_NOOP("SettingsViewModel", "跟随配置"),
    # mihomo's names for its stacks.
    "system": "System",
    "mips": "Mips",
    "gvisor": "gVisor",
    "mixed": "Mixed",
}
# Only macOS's NetworkExtension has the setting (D19).
INCLUDE_ALL_NETWORKS = sys.platform == "darwin"


@QmlElement
@QmlUncreatable("SettingsViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class SettingsViewModel(QObject):
    changed = Signal()
    optionsChanged = Signal()  # the UI language changed; the choices' labels follow
    languageChanged = Signal()  # the app installs the new language (app.switch_language)
    appearanceChanged = Signal()  # the theme's appearance follows ``appearance``
    # Not ``changed``: that fires while the user types a port, which would undo the typing.
    mixedPortChanged = Signal()

    def __init__(
        self,
        settings: SettingsStore,
        library: ProfileLibrary,
        host: TunnelHost,
        runner: AsyncRunner,
        login_item: LoginItem,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        # Whoever changes the settings, the page follows: this page, OverviewViewModel's
        # switches, or a restored backup.
        settings.subscribe(self._settings_changed)
        self._library = library
        self._host = host
        self._runner = runner
        self._login_item = login_item
        self._login = LoginItemStatus(enabled=False)
        self._login_busy = False
        self._login_error = ""
        self._applying = False
        self._port_error = ""
        self._lan_error = ""
        self._stack_error = ""
        self._error = ""
        self._info: HostInfo | None = None

    def start(self) -> None:
        self.refreshInfo()
        self.refreshLoginItem()

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self.optionsChanged.emit()
        self.changed.emit()

    def _current(self) -> Settings:
        return self._settings.current

    def _settings_changed(self, before: Settings, after: Settings) -> None:
        if before.language != after.language:
            self.languageChanged.emit()
        if before.appearance != after.appearance:
            self.appearanceChanged.emit()
        if before.mixed_port != after.mixed_port:
            self.mixedPortChanged.emit()
        self.changed.emit()

    def _store(self, **changes: Any) -> None:
        """Write ``changes``; the subscription updates the page."""
        failed = self._error
        self._error = ""
        try:
            self._settings.update(**changes)
        except SettingsError as error:
            self._error = failure_text(error)
        if self._error or failed:
            self.changed.emit()

    @Property(str, notify=changed)
    def error(self) -> str:
        """Why the last change could not be stored."""
        return self._error

    # -- UI language and appearance ----------------------------------------------------------
    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._current().language

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def languageOptions(self) -> list[dict[str, str]]:
        return [{"value": v, "label": self.tr(LANGUAGE_LABELS[v])} for v in LANGUAGES]

    @Slot(str)
    def setLanguage(self, language: str) -> None:
        if language in LANGUAGES:
            self._store(language=language)

    @Property(str, notify=appearanceChanged)
    def appearance(self) -> str:
        """``system``, ``light`` or ``dark``: the colours the window should use."""
        return self._current().appearance

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def appearanceOptions(self) -> list[dict[str, str]]:
        return [{"value": v, "label": self.tr(APPEARANCE_LABELS[v])} for v in APPEARANCES]

    @Slot(str)
    def setAppearance(self, appearance: str) -> None:
        if appearance in APPEARANCES:
            self._store(appearance=appearance)

    # -- desktop -----------------------------------------------------------------------------
    @Property(str, notify=changed)
    def closeAction(self) -> str:
        return self._current().close_action

    @Property("QVariantList", notify=optionsChanged)  # pyright: ignore[reportArgumentType]
    def closeActionOptions(self) -> list[dict[str, str]]:
        return [{"value": v, "label": self.tr(CLOSE_ACTION_LABELS[v])} for v in CLOSE_ACTIONS]

    @Slot(str)
    def setCloseAction(self, action: str) -> None:
        if action in CLOSE_ACTIONS:
            self._store(close_action=action)

    @Property(bool, notify=changed)
    def launchAtLoginAvailable(self) -> bool:
        return self._login_item.available and not self._login_busy

    @Property(bool, notify=changed)
    def launchAtLogin(self) -> bool:
        return self._login.enabled

    @Property(bool, notify=changed)
    def launchAtLoginNeedsApproval(self) -> bool:
        return self._login.needs_approval

    @Property(str, notify=changed)
    def launchAtLoginDetail(self) -> str:
        if self._login_error:
            return self._login_error
        if not self._login_item.available:
            return self.tr("仅安装版可用")
        if self._login.needs_approval:
            return self.tr("需要在系统设置的登录项中允许")
        return ""

    @Slot()
    def refreshLoginItem(self) -> None:
        """Read the system's login item: the user may have removed it there."""
        if self._login_item.available:
            self._run_login(asyncio.to_thread(self._login_item.status))

    @Slot(bool)
    def setLaunchAtLogin(self, enabled: bool) -> None:
        if self._login_item.available and not self._login_busy:
            self._run_login(asyncio.to_thread(self._login_item.set_enabled, enabled))

    @Slot()
    def openLoginItemSettings(self) -> None:
        self._runner.submit(asyncio.to_thread(self._login_item.open_settings))

    def _run_login(self, action: Any) -> None:
        self._login_busy = True
        self._login_error = ""
        self.changed.emit()

        def done(status: LoginItemStatus) -> None:
            self._login_busy = False
            self._login = status
            self._store(launch_at_login=status.enabled)
            self.changed.emit()

        def failed(error: BaseException) -> None:
            self._login_busy = False
            self._login_error = failure_text(error)
            self.changed.emit()

        self._runner.submit(action, done, failed)

    # -- run options -------------------------------------------------------------------------
    @Property(int, notify=mixedPortChanged)
    def mixedPort(self) -> int:
        return self._current().mixed_port

    @Property(str, notify=changed)
    def portError(self) -> str:
        return self._port_error

    @Property(bool, notify=changed)
    def allowLan(self) -> bool:
        return self._current().allow_lan

    @Property(str, notify=changed)
    def lanError(self) -> str:
        return self._lan_error

    @Property(bool, notify=changed)
    def applying(self) -> bool:
        return self._applying

    @Property(str, notify=changed)
    def tunStack(self) -> str:
        return self._current().tun_stack

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def tunStackOptions(self) -> list[dict[str, object]]:
        """The stacks, with the ones a tunnel that includes all networks cannot run disabled."""
        options: list[dict[str, object]] = []
        for value in TUN_STACK_CHOICES:
            option: dict[str, object] = {"value": value, "label": self.tr(TUN_STACK_LABELS[value])}
            if self._all_networks() and value in ALL_NETWORKS_BREAKS:
                option.update(enabled=False, note=self.tr("包含所有网络时不可用"))
            options.append(option)
        return options

    @Property(str, notify=changed)
    def stackError(self) -> str:
        return self._stack_error

    @Property(bool, constant=True)
    def includeAllNetworksAvailable(self) -> bool:
        return INCLUDE_ALL_NETWORKS

    @Property(bool, notify=changed)
    def includeAllNetworks(self) -> bool:
        return self._current().include_all_networks

    def _all_networks(self) -> bool:
        return INCLUDE_ALL_NETWORKS and self._current().include_all_networks

    @Slot(str)
    def setMixedPort(self, text: str) -> None:
        try:
            port = int(text.strip())
        except ValueError:
            port = 0
        if not valid_port(port):
            self._port_error = self.tr("端口应在 {low}–{high} 之间").format(
                low=MIN_MIXED_PORT, high=MAX_MIXED_PORT
            )
            self.changed.emit()
            return
        self._port_error = ""
        if port == self._current().mixed_port:
            self.changed.emit()
            return
        self._apply("port", mixed_port=port)

    @Slot(bool)
    def setAllowLan(self, allowed: bool) -> None:
        if allowed != self._current().allow_lan:
            self._apply("lan", allow_lan=allowed)

    @Slot(str)
    def setTunStack(self, stack: str) -> None:
        if stack not in TUN_STACK_CHOICES or stack == self._current().tun_stack:
            return
        if self._all_networks() and stack in ALL_NETWORKS_BREAKS:
            return
        self._apply("stack", tun_stack=stack)

    @Slot(bool)
    def setIncludeAllNetworks(self, included: bool) -> None:
        """Stored for the next connection; a stack that cannot run with it becomes gVisor,
        which the running tunnel takes at once."""
        current = self._current()
        if not INCLUDE_ALL_NETWORKS or included == current.include_all_networks:
            return
        if included and current.tun_stack in ALL_NETWORKS_BREAKS:
            self._apply("stack", include_all_networks=True, tun_stack="gvisor")
        else:
            self._store(include_all_networks=included)

    def _apply(self, field: str, **changes: Any) -> None:
        """Reload the running tunnel with ``changes`` to the settings, then store them; a
        refusal keeps the stored ones and shows under the ``field`` row."""
        if self._applying:
            return
        self._applying = True
        self._port_error = self._lan_error = self._stack_error = ""
        self.changed.emit()

        def applied(_: object) -> None:
            self._applying = False
            self._store(**changes)
            self.changed.emit()

        def failed(error: BaseException) -> None:
            self._applying = False
            if field == "port":
                self._port_error = failure_text(error)
            elif field == "lan":
                self._lan_error = failure_text(error)
            else:
                self._stack_error = failure_text(error)
            self.changed.emit()

        settings = replace(self._current(), **changes)
        self._runner.submit(self._library.apply_settings(settings), applied, failed)

    # -- about -------------------------------------------------------------------------------
    @Slot()
    def refreshInfo(self) -> None:
        self._runner.submit(self._host.get_info(), self._info_read, self._info_failed)

    def _info_read(self, info: HostInfo) -> None:
        self._info = info
        self.changed.emit()

    def _info_failed(self, _: BaseException) -> None:
        self._info = None
        self.changed.emit()

    @Property(str, constant=True)
    def version(self) -> str:
        return __version__

    @Property(str, notify=changed)
    def serviceVersion(self) -> str:
        return self._info.service_version if self._info else "—"

    @Property(str, notify=changed)
    def coreVersion(self) -> str:
        return self._info.core_version if self._info else "—"

    @Property(str, notify=changed)
    def protocolVersion(self) -> str:
        return f"v{self._info.protocol_version}" if self._info else "—"

    @Property(str, constant=True)
    def license(self) -> str:
        return LICENSE

    @Property(str, constant=True)
    def projectUrl(self) -> str:
        return PROJECT_URL

    @Property(str, constant=True)
    def projectLabel(self) -> str:
        return PROJECT_URL.removeprefix("https://")
