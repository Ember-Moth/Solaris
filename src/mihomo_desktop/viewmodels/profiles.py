"""Profiles ViewModel: the subscription list (docs/routing.md §4.2), imports, updates, the node
pool, and the profile in use that the overview shows."""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import Property, QCoreApplication, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QmlElement, QmlUncreatable

from mihomo_desktop.models import format as fmt
from mihomo_desktop.models.library import (
    DEFAULT_AUTO_UPDATE_HOURS,
    ProfileLibrary,
    Removal,
    Routing,
)
from mihomo_desktop.models.profiles import Profile, ProfileList
from mihomo_desktop.models.routing import MODE_PLAN, MODE_PROFILE
from mihomo_desktop.viewmodels.common.async_runner import AsyncRunner
from mihomo_desktop.viewmodels.common.keyed_list import KeyedListModel
from mihomo_desktop.viewmodels.feedback import failure_text

QML_IMPORT_NAME = "MihomoDesktop"
QML_IMPORT_MAJOR_VERSION = 1

# Checks which subscriptions are due and keeps "2 小时前" current.
TICK_MS = 60_000


@dataclass(frozen=True)
class ProfileRow:
    """One row of the profile table, as text."""

    id: str
    name: str
    source: str
    active: bool
    usage: str
    hasQuota: bool
    usedFraction: float
    expiry: str
    expired: bool
    updated: str
    updateFailed: bool
    updating: bool
    refreshLabel: str
    isSubscription: bool
    autoUpdate: bool
    hasOverride: bool
    note: str  # 规则使用中 or 不加入节点池, after the source in the caption
    warning: str  # 更新失败, in the warning colour (DESIGN.md "分组与行"); empty otherwise
    inPool: bool
    hasRules: bool  # it brings rules, or has not been read yet

    @property
    def profileId(self) -> str:  # QML reserves ``id``
        return self.id


ROLES = (*(f.name for f in fields(ProfileRow) if f.name != "id"), "profileId")


def _origin(profile: Profile) -> str:
    """Where the profile comes from: the subscription's host or the file's name."""
    if profile.is_subscription:
        return urlsplit(profile.url).hostname or profile.url
    return Path(profile.path).name


# Module-level text is translated in the ViewModel's context, as self.tr() would.
def _source(profile: Profile) -> str:
    if profile.is_subscription:
        kind = QCoreApplication.translate("ProfilesViewModel", "订阅")
    else:
        kind = QCoreApplication.translate("ProfilesViewModel", "本地文件")
    parts = [kind, _origin(profile)]
    if profile.proxy_count:
        count = profile.proxy_count
        parts.append(QCoreApplication.translate("ProfilesViewModel", "%n 个节点", None, count))
    if profile.has_override:
        parts.append(QCoreApplication.translate("ProfilesViewModel", "已覆写"))
    return " · ".join(parts)


def _usage(profile: Profile) -> tuple[str, float | None]:
    """The usage text, and the used fraction when the subscription has a quota."""
    usage = profile.usage
    if usage is None:
        return "—", None
    if not usage.total:
        return fmt.size(usage.used), None
    return fmt.usage(usage.used, usage.total), min(usage.used / usage.total, 1.0)


def _removal_text(removal: Removal) -> str:
    """The consequences of a removal, stated in its confirmation (routing.md §5.1)."""
    if removal.profile.has_override:
        deleted = QCoreApplication.translate(
            "ProfilesViewModel", "订阅和它的覆写会从本机删除，无法恢复。"
        )
    else:
        deleted = QCoreApplication.translate("ProfilesViewModel", "订阅会从本机删除，无法恢复。")
    # Whole sentences, so that a translation can put a space between them.
    if removal.active and removal.successor is not None:
        text = QCoreApplication.translate(
            "ProfilesViewModel",
            "这是正在使用规则的订阅，删除后改用「{successor}」的规则。{deleted}",
        )
        return text.format(successor=removal.successor.name, deleted=deleted)
    if removal.active:
        text = QCoreApplication.translate(
            "ProfilesViewModel", "这是正在使用规则的订阅，删除后改用我的方案。{deleted}"
        )
        return text.format(deleted=deleted)
    if removal.pool_nodes:
        text = QCoreApplication.translate(
            "ProfilesViewModel", "节点池会少 %n 个节点。{deleted}", None, removal.pool_nodes
        )
        return text.format(deleted=deleted)
    return deleted


def _expiry(profile: Profile, now: float) -> tuple[str, bool]:
    """The expiry date, and whether it has passed."""
    if profile.usage is None or not profile.usage.expire:
        return "—", False
    if profile.usage.expire <= now:
        return QCoreApplication.translate("ProfilesViewModel", "已过期"), True
    return fmt.date(profile.usage.expire), False


@QmlElement
@QmlUncreatable("ProfilesViewModel is created in Python and injected into the view.")  # pyright: ignore[reportCallIssue]
class ProfilesViewModel(QObject):
    changed = Signal()
    applied = Signal()  # the tunnel may run another config now; groups need a refresh
    imported = Signal()  # an import succeeded; the view closes its form
    removalRequested = Signal()  # a removal awaits confirmation; the view asks
    overrideOpened = Signal()  # an override was read; the view opens its editor
    overrideSaved = Signal()  # the host accepted the override and it is stored
    routingChanged = Signal()  # the pool or the profile in use may have changed
    routingRequested = Signal()  # 使用它的规则: the view goes to the routing page

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
        self._routing: Routing = library.routing_snapshot()
        self._updating: set[str] = set()
        self._importing = False
        self._import_error = ""
        self._checking = False
        self._error = ""
        self._removal: Removal | None = None  # the last one asked about; its text stays
        self._removal_pending = False  # while the dialog closes
        self._override_target: str | None = None  # the profile id; None is the global one
        self._override_name = ""
        self._override_text = ""
        self._override_saving = False
        self._override_error = ""
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.checkUpdates)
        # Rows update in place, so the table keeps its delegates and the keyboard focus.
        self._model = KeyedListModel(ROLES, self)
        self.changed.connect(self._sync_rows)
        self._sync_rows()

    def start(self) -> None:
        self.checkUpdates()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def retranslate(self) -> None:
        """Re-read the text properties in the language just installed."""
        self.changed.emit()

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
            self._routing_touched()
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

    def _routing_touched(self) -> None:
        """Read the routing again after a change to the profiles, and let the routing page
        know."""
        self._runner.submit(self._library.routing(), self._routing_loaded)
        self.routingChanged.emit()

    def _routing_loaded(self, routing: Routing) -> None:
        self._routing = routing
        self.changed.emit()

    @Slot()
    def reload(self) -> None:
        """Take up what the routing page changed."""

        async def read() -> tuple[ProfileList, Routing]:
            return await self._library.current(), await self._library.routing()

        def done(result: tuple[ProfileList, Routing]) -> None:
            self._list, self._routing = result
            self.changed.emit()

        self._runner.submit(read(), done)

    @Slot()
    def inspect(self) -> None:
        """Read the profiles not read yet, to know which bring rules."""
        self._runner.submit(self._library.inspect_all(), self._routing_loaded)

    # -- list --------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def summary(self) -> str:
        if self._error:
            return self._error
        count = self.tr("%n 份订阅", "", len(self._list.profiles))
        nodes = self._pool_nodes()
        if self._routing.mode != MODE_PLAN or nodes is None:
            return count
        pool = self.tr("节点池 %n 个节点", "", nodes)
        return self.tr("{count} · {pool}").format(count=count, pool=pool)

    def _pool_nodes(self) -> int | None:
        report = self._routing.report
        if report is not None:
            return len(report.nodes)
        total = 0
        for profile in self._list.profiles:
            if not profile.in_pool:
                continue
            inspection = self._routing.inspections.get(profile.id)
            if inspection is None:
                return None
            total += sum(not node.info for node in inspection.nodes)
        return total

    @Property(bool, notify=changed)
    def hasError(self) -> bool:
        return bool(self._error)

    @Property(QObject, constant=True)
    def model(self) -> KeyedListModel:
        """The profiles as ProfileRow items."""
        return self._model

    def _sync_rows(self) -> None:
        now = self._clock()
        self._model.sync([self._row(profile, now) for profile in self._list.profiles])

    def _row(self, profile: Profile, now: float) -> ProfileRow:
        usage, fraction = _usage(profile)
        expiry, expired = _expiry(profile, now)
        updating = profile.id in self._updating
        if updating:
            updated = self.tr("正在更新")
        elif profile.id in self._list.update_failed:
            updated = self.tr("更新失败")
        else:
            updated = fmt.ago(profile.updated_at, now)
        return ProfileRow(
            id=profile.id,
            name=profile.name,
            source=_source(profile),
            active=profile.id == self._list.active,
            usage=usage,
            hasQuota=fraction is not None,
            usedFraction=fraction or 0.0,
            expiry=expiry,
            expired=expired,
            updated=updated,
            updateFailed=profile.id in self._list.update_failed and not updating,
            updating=updating,
            refreshLabel=self._refresh_label(profile),
            isSubscription=profile.is_subscription,
            autoUpdate=profile.auto_update_hours > 0,
            hasOverride=profile.has_override,
            note=self._note(profile),
            warning=self.tr("更新失败")
            if profile.id in self._list.update_failed and not updating
            else "",
            inPool=profile.in_pool,
            hasRules=self._routing.has_rules(profile.id) is not False,
        )

    def _note(self, profile: Profile) -> str:
        if self._routing.mode == MODE_PROFILE and profile.id == self._list.active:
            return self.tr("规则使用中")
        if self._routing.mode == MODE_PLAN and not profile.in_pool:
            return self.tr("不加入节点池")
        return ""

    @Property(str, notify=changed)
    def autoUpdateLabel(self) -> str:
        return self.tr("每 %n 小时自动更新", "", DEFAULT_AUTO_UPDATE_HOURS)

    def _refresh_label(self, profile: Profile) -> str:
        return self.tr("更新") if profile.is_subscription else self.tr("重新读取")

    @Slot(str)
    def useRules(self, profile_id: str) -> None:
        """使用它的规则: route with this profile's own rules, as the routing page would, and
        go there."""
        self.routingRequested.emit()
        if profile_id != self._list.active or self._routing.mode != MODE_PROFILE:
            self._run(self._library.activate(profile_id), reloads=True)

    @Slot(str, bool)
    def setInPool(self, profile_id: str, in_pool: bool) -> None:
        reloads = self._routing.mode == MODE_PLAN
        self._run(self._library.set_in_pool(profile_id, in_pool), reloads=reloads)

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

    # -- removal -----------------------------------------------------------------------------
    @Slot(str)
    def requestRemove(self, profile_id: str) -> None:
        """Work out what removing the profile does, then ask the view to confirm it. The
        profile in use while the tunnel runs is refused without asking."""
        self._error = ""
        self.changed.emit()

        def ready(removal: Removal) -> None:
            self._removal = removal
            self._removal_pending = True
            self.changed.emit()
            self.removalRequested.emit()

        def failed(error: BaseException) -> None:
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.removal(profile_id), ready, failed)

    @Property(str, notify=changed)
    def removalTitle(self) -> str:
        if self._removal is None:
            return ""
        return self.tr("删除「{name}」？").format(name=self._removal.profile.name)

    @Property(str, notify=changed)
    def removalText(self) -> str:
        return _removal_text(self._removal) if self._removal else ""

    @Slot()
    def confirmRemove(self) -> None:
        if self._removal is not None and self._removal_pending:
            self._removal_pending = False
            removal = self._removal
            reloads = removal.active or bool(removal.pool_nodes)
            self._run(self._library.remove(removal.profile.id), reloads=reloads)

    @Slot()
    def cancelRemove(self) -> None:
        self._removal_pending = False

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

    # -- overrides ---------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def hasGlobalOverride(self) -> bool:
        return self._list.has_global_override

    @Property(str, notify=changed)
    def globalOverrideDetail(self) -> str:
        if self._list.has_global_override:
            return self.tr("两种分流方式都生效 · 已设置")
        return self.tr("两种分流方式都生效 · 未设置")

    @Property(bool, notify=changed)
    def canEditGlobalOverride(self) -> bool:
        """The global override is checked with the routing in use, so there must be one."""
        return self._routing.mode == MODE_PLAN or self._list.active is not None

    @Property(str, notify=changed)
    def overrideScope(self) -> str:
        """Where the override being edited applies (routing.md §5.6)."""
        if self._override_target is None:
            return self.tr("对“订阅自带”和“我的方案”都生效。")
        if self._routing.mode == MODE_PROFILE and self._override_target == self._list.active:
            return self.tr("只在“订阅自带”方式下使用这份订阅的规则时生效，现在正在生效。")
        return self.tr("只在“订阅自带”方式下使用这份订阅的规则时生效，现在不生效。")

    @Slot(str)
    def editOverride(self, profile_id: str) -> None:
        profile = next((p for p in self._list.profiles if p.id == profile_id), None)
        if profile is not None:
            self._open_override(profile_id, profile.name)

    @Slot()
    def editGlobalOverride(self) -> None:
        self._open_override(None, "")

    def _open_override(self, profile_id: str | None, name: str) -> None:
        """Read the stored text, then let the view open the editor on it."""
        self._error = ""

        def read(text: str) -> None:
            self._override_target, self._override_name = profile_id, name
            self._override_text = text
            self._override_error = ""
            self._override_saving = False
            self.changed.emit()
            self.overrideOpened.emit()

        def failed(error: BaseException) -> None:
            self._error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.read_override(profile_id), read, failed)

    @Property(str, notify=changed)
    def overrideTitle(self) -> str:
        if self._override_target is None:
            return self.tr("编辑全局覆写")
        return self.tr("编辑「{name}」的覆写").format(name=self._override_name)

    @Property(str, notify=changed)
    def overrideText(self) -> str:
        """The stored text when the editor opened."""
        return self._override_text

    @Property(bool, notify=changed)
    def overrideSaving(self) -> bool:
        return self._override_saving

    @Property(str, notify=changed)
    def overrideError(self) -> str:
        return self._override_error

    @Slot(str)
    def saveOverride(self, text: str) -> None:
        """Validate and store the editor's text. A rejection stays in the editor; success
        closes it, and refreshes the groups when the tunnel runs the profile affected."""
        if self._override_saving:
            return
        target = self._override_target
        reloads = target is None or (
            target == self._list.active and self._routing.mode == MODE_PROFILE
        )
        self._override_saving = True
        self._override_error = ""
        self.changed.emit()

        def saved(profiles: ProfileList) -> None:
            self._override_saving = False
            self._override_text = text
            self._loaded(profiles)
            self.overrideSaved.emit()
            if reloads:
                self.applied.emit()

        def failed(error: BaseException) -> None:
            self._override_saving = False
            self._override_error = failure_text(error)
            self.changed.emit()

        self._runner.submit(self._library.save_override(target, text), saved, failed)

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
            self._routing_touched()
            self.applied.emit()
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
        """Where the profile in use comes from and when it was updated; a failed update is
        ``activeWarning``, shown in the warning colour after it (DESIGN.md "分组与行")."""
        active = self._active()
        if active is None:
            return ""
        if active.id in self._updating:
            return self.tr("{origin} · 正在更新").format(origin=_origin(active))
        if active.id in self._list.update_failed:
            return _origin(active)
        ago = fmt.ago(active.updated_at, self._clock())
        return self.tr("{origin} · {ago}更新").format(origin=_origin(active), ago=ago)

    @Property(str, notify=changed)
    def activeWarning(self) -> str:
        active = self._active()
        if active is None or active.id in self._updating:
            return ""
        return self.tr("更新失败") if active.id in self._list.update_failed else ""

    @Property(bool, notify=changed)
    def activeUpdating(self) -> bool:
        active = self._active()
        return active is not None and active.id in self._updating

    @Property(str, notify=changed)
    def activeRefreshLabel(self) -> str:
        active = self._active()
        return self._refresh_label(active) if active else self.tr("更新")

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
        return text if expired else self.tr("{date} 到期").format(date=text)
