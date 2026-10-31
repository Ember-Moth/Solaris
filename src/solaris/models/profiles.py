"""Profiles: configs the user imported from a subscription link or a local file."""

from __future__ import annotations

from dataclasses import dataclass, replace

KIND_SUBSCRIPTION = "subscription"
KIND_FILE = "file"


class ProfileError(Exception):
    """A profile could not be downloaded, read or stored. Codes share the HostError
    namespace so the UI turns both into text the same way."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Usage:
    """The ``Subscription-Userinfo`` response header."""

    upload: int
    download: int
    total: int  # 0 when the subscription has no quota
    expire: int  # unix seconds; 0 when it does not expire

    @property
    def used(self) -> int:
        return self.upload + self.download


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    kind: str  # KIND_SUBSCRIPTION or KIND_FILE
    url: str = ""  # the subscription link
    path: str = ""  # the file a KIND_FILE profile was imported from
    auto_update_hours: int = 0  # subscriptions only; 0 turns automatic updates off
    updated_at: float = 0.0  # unix seconds of the last download or import
    usage: Usage | None = None
    # The nodes it brings to the node pool (SourceInspection.node_count); None until read.
    proxy_count: int | None = None
    has_override: bool = False  # it has an override of its own; read from the overrides
    in_pool: bool = True  # its nodes join the node pool of the user's plans (routing.md §5.2)
    # Whether the stored config brings rules of its own; None until a core read it.
    has_rules: bool | None = None
    # Stored while no core could read it (macOS, tunnel down; D27): the core reads it at the
    # next connect. Until then it has no node count and may or may not bring rules.
    unread: bool = False

    @property
    def is_subscription(self) -> bool:
        return self.kind == KIND_SUBSCRIPTION


@dataclass(frozen=True)
class ProfileList:
    profiles: tuple[Profile, ...] = ()
    active: str | None = None  # the id of the profile the tunnel uses
    update_failed: frozenset[str] = frozenset()  # ids whose last update failed; not stored
    has_global_override: bool = False  # there is a global override; read from the overrides

    def get(self, profile_id: str) -> Profile:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileError("unknown_profile", f"no profile {profile_id}")

    def active_profile(self) -> Profile | None:
        return next((p for p in self.profiles if p.id == self.active), None)

    def put(self, profile: Profile) -> ProfileList:
        """Replace the profile with the same id, or append it."""
        if any(p.id == profile.id for p in self.profiles):
            profiles = tuple(profile if p.id == profile.id else p for p in self.profiles)
        else:
            profiles = (*self.profiles, profile)
        return replace(self, profiles=profiles)

    def without(self, profile_id: str) -> ProfileList:
        profiles = tuple(p for p in self.profiles if p.id != profile_id)
        active = self.active
        if active == profile_id:
            active = profiles[0].id if profiles else None
        return replace(
            self, profiles=profiles, active=active, update_failed=self.update_failed - {profile_id}
        )
