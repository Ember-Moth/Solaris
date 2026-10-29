"""Profiles ViewModel: the profile list, imports, updates and the active profile that the
overview and the sidebar show."""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.models import format as fmt
from mihomo_desktop.models.library import DEFAULT_AUTO_UPDATE_HOURS, ProfileLibrary
from mihomo_desktop.models.profiles import Profile, ProfileList
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

# Checks which subscriptions are due and keeps "2 小时前" current.
TICK_MS = 60_000


def _origin(profile: Profile) -> str:
    """Where the profile comes from: the subscription's host or the file's name."""
    if profile.is_subscription:
        return urlsplit(profile.url).hostname or profile.url
    return Path(profile.path).name


def _source(profile: Profile) -> str:
    parts = ["订阅" if profile.is_subscription else "本地文件", _origin(profile)]
    if profile.proxy_count:
        parts.append(f"{profile.proxy_count} 个节点")
    return " · ".join(parts)


def _usage(profile: Profile) -> tuple[str, float | None]:
    """The usage text, and the used fraction when the subscription has a quota."""
    usage = profile.usage
    if usage is None:
        return "—", None
    if not usage.total:
        return fmt.size(usage.used), None
    return fmt.usage(usage.used, usage.total), min(usage.used / usage.total, 1.0)


def _expiry(profile: Profile, now: float) -> tuple[str, bool]:
    """The expiry date, and whether it has passed."""
    if profile.usage is None or not profile.usage.expire:
        return "—", False
    if profile.usage.expire <= now:
        return "已过期", True
    return fmt.date(profile.usage.expire), False


@QmlElement
@QmlUncreatable("ProfilesViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ProfilesViewModel(QObject):
    changed = Signal()
    applied = Signal()  # the tunnel may run another config now; groups need a refresh
    imported = Signal()  # an import succeeded; the view closes its form

    def __init__(
        self,
        library: ProfileLibrary,
        runner: AsyncRunner,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._runner = runner
        self._clock = clock
        self._list: ProfileList = library.snapshot()
        self._updating: set[str] = set()
        self._importing = False
        self._import_error = ""
        self._checking = False
        self._error = ""
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.checkUpdates)

    def start(self) -> None:
        self.checkUpdates()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _loaded(self, profiles: ProfileList) -> None:
        self._list = profiles
        self.changed.emit()

    def _run(
        self,
        action: Coroutine[Any, Any, ProfileList],
        done: Callable[[], None] | None = None,
        *,
        reloads: bool = False,
    ) -> None:
        """Submit a library action; its result replaces the list, its failure the status."""
        self._error = ""
        self.changed.emit()

        def succeeded(profiles: ProfileList) -> None:
            if done is not None:
                done()
            self._error = ""
            self._loaded(profiles)
            if reloads:
                self.applied.emit()

        def failed(error: BaseException) -> None:
            if done is not None:
                done()
            self._error = failure_text(error)
            self.changed.emit()
            # A failed update still marks its profile; fetch the list that says so.
            self._runner.submit(self._library.current(), self._loaded)

        self._runner.submit(action, succeeded, failed)

    # -- list --------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def summary(self) -> str:
        return self._error or f"{len(self._list.profiles)} 个配置"

    @Property(bool, notify=changed)
    def hasError(self) -> bool:
        return bool(self._error)

    @Property("QVariantList", notify=changed)  # pyright: ignore[reportArgumentType]
    def profiles(self) -> list[dict[str, object]]:
        now = self._clock()
        rows: list[dict[str, object]] = []
        for profile in self._list.profiles:
            usage, fraction = _usage(profile)
            expiry, expired = _expiry(profile, now)
            updating = profile.id in self._updating
            if updating:
                updated = "正在更新"
            elif profile.id in self._list.update_failed:
                updated = "更新失败"
            else:
                updated = fmt.ago(profile.updated_at, now)
            rows.append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "source": _source(profile),
                    "active": profile.id == self._list.active,
                    "usage": usage,
                    "hasQuota": fraction is not None,
                    "usedFraction": fraction or 0.0,
                    "expiry": expiry,
                    "expired": expired,
                    "updated": updated,
                    "updateFailed": profile.id in self._list.update_failed and not updating,
                    "updating": updating,
                    "refreshLabel": "更新" if profile.is_subscription else "重新读取",
                    "isSubscription": profile.is_subscription,
                    "autoUpdate": profile.auto_update_hours > 0,
                }
            )
        return rows

    @Property(str, constant=True)
    def autoUpdateLabel(self) -> str:
        return f"每 {DEFAULT_AUTO_UPDATE_HOURS} 小时自动更新"

    @Slot(str)
    def use(self, profile_id: str) -> None:
        if profile_id != self._list.active:
            self._run(self._library.activate(profile_id), reloads=True)

    @Slot(str)
    def updateProfile(self, profile_id: str) -> None:
        if profile_id in self._updating:
            return
        self._updating.add(profile_id)
        reloads = profile_id == self._list.active
        self._run(
            self._library.update(profile_id),
            lambda: self._updating.discard(profile_id),
            reloads=reloads,
        )

    @Slot(str, bool)
    def setAutoUpdate(self, profile_id: str, enabled: bool) -> None:
        self._run(self._library.set_auto_update(profile_id, enabled))

    @Slot(str)
    def removeProfile(self, profile_id: str) -> None:
        self._run(self._library.remove(profile_id))

    @Slot(str)
    def copyLink(self, profile_id: str) -> None:
        profile = next((p for p in self._list.profiles if p.id == profile_id), None)
        if profile is not None and profile.url:
            QGuiApplication.clipboard().setText(profile.url)

    @Slot()
    def checkUpdates(self) -> None:
        """Update the subscriptions that are due; also refreshes the relative times."""
        if self._checking:
            self.changed.emit()
            return
        self._checking = True

        def checked(profiles: ProfileList) -> None:
            self._checking = False
            reloaded = any(
                p.id == profiles.active and p.updated_at != self._updated_at(p.id)
                for p in profiles.profiles
            )
            self._loaded(profiles)
            if reloaded:
                self.applied.emit()

        def failed(error: BaseException) -> None:
            self._checking = False
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.update_due(), checked, failed)

    def _updated_at(self, profile_id: str) -> float | None:
        return next((p.updated_at for p in self._list.profiles if p.id == profile_id), None)

    # -- import ------------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def importing(self) -> bool:
        return self._importing

    @Property(str, notify=changed)
    def importError(self) -> str:
        return self._import_error

    @Slot()
    def clearImportError(self) -> None:
        if self._import_error:
            self._import_error = ""
            self.changed.emit()

    def _import(self, action: Coroutine[Any, Any, ProfileList], *, from_link: bool) -> None:
        """Link imports report failures in their form; file imports in the page header."""
        if self._importing:
            action.close()
            return
        self._importing = True
        self._import_error = ""
        self._error = ""
        self.changed.emit()

        def imported(profiles: ProfileList) -> None:
            self._importing = False
            self._loaded(profiles)
            self.imported.emit()

        def failed(error: BaseException) -> None:
            self._importing = False
            if from_link:
                self._import_error = failure_text(error)
            else:
                self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(action, imported, failed)

    @Slot(str, str, bool)
    def importUrl(self, url: str, name: str, auto_update: bool) -> None:
        self._import(self._library.import_url(url, name, auto_update), from_link=True)

    @Slot(str)
    def importFile(self, file_url: str) -> None:
        """Import the file a FileDialog chose; it passes a file:// URL."""
        path = QUrl(file_url).toLocalFile() or file_url
        self._import(self._library.import_file(Path(path)), from_link=False)

    # -- active profile (overview and sidebar) -----------------------------------------------
    def _active(self) -> Profile | None:
        return self._list.active_profile()

    @Property(bool, notify=changed)
    def hasActive(self) -> bool:
        return self._active() is not None

    @Property(str, notify=changed)
    def activeId(self) -> str:
        active = self._active()
        return active.id if active else ""

    @Property(str, notify=changed)
    def activeName(self) -> str:
        active = self._active()
        return active.name if active else ""

    @Property(str, notify=changed)
    def activeDetail(self) -> str:
        active = self._active()
        if active is None:
            return ""
        if active.id in self._updating:
            return f"{_origin(active)} · 正在更新"
        if active.id in self._list.update_failed:
            return f"{_origin(active)} · 更新失败"
        return f"{_origin(active)} · {fmt.ago(active.updated_at, self._clock())}更新"

    @Property(bool, notify=changed)
    def activeUpdating(self) -> bool:
        active = self._active()
        return active is not None and active.id in self._updating

    @Property(str, notify=changed)
    def activeRefreshLabel(self) -> str:
        active = self._active()
        return "重新读取" if active and not active.is_subscription else "更新"

    @Property(bool, notify=changed)
    def activeHasQuota(self) -> bool:
        active = self._active()
        return active is not None and _usage(active)[1] is not None

    @Property(str, notify=changed)
    def activeUsage(self) -> str:
        active = self._active()
        return _usage(active)[0] if active else ""

    @Property(float, notify=changed)
    def activeUsedFraction(self) -> float:
        active = self._active()
        return (_usage(active)[1] or 0.0) if active else 0.0

    @Property(str, notify=changed)
    def activeExpiry(self) -> str:
        active = self._active()
        if active is None:
            return ""
        text, expired = _expiry(active, self._clock())
        if text == "—":
            return ""
        return text if expired else f"{text} 到期"
