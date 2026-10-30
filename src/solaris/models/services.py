"""The service list that ships with the app (docs/routing.md §7): what a "service" rule entry
matches, and where the add-rule dialog suggests sending it.

A plan stores a service entry by its id; the entry is expanded with the list of the running
version when the plan is composed, so a newer list applies to plans made with an older one.
Each service is one service or one purpose: there are no bundles such as "AI services",
which would overlap the single ones. Every category named here is in the GEO data the
installers ship (host/geo_categories.py; tests/test_services.py checks it).

A Python module rather than a data file, as host/geo_categories.py, so Nuitka takes it along.
"""

from __future__ import annotations

from dataclasses import dataclass

from solaris.i18n import QT_TRANSLATE_NOOP

GEOSITE = "GEOSITE"
GEOIP = "GEOIP"

# Suggested targets besides region codes, as rule targets in a plan.
PROXY = "proxy"  # 节点选择
DIRECT = "DIRECT"
REJECT = "REJECT"


@dataclass(frozen=True)
class Match:
    """One rule a service expands to, without its target."""

    type: str  # GEOSITE or GEOIP
    value: str
    no_resolve: bool = False

    def item(self) -> dict[str, object]:
        """The match as an item of plan JSON (routing.md §8.5)."""
        item: dict[str, object] = {"type": self.type, "value": self.value}
        if self.no_resolve:
            item["no_resolve"] = True
        return item


@dataclass(frozen=True)
class Category:
    id: str
    name: str  # translated in the "Services" context


@dataclass(frozen=True)
class Service:
    id: str
    name_zh: str
    name_en: str
    category: str  # a Category id
    matches: tuple[Match, ...]
    # A region code ("US"), PROXY, DIRECT or REJECT.
    suggested: str

    def name(self, language: str) -> str:
        return self.name_en if language == "en" else self.name_zh

    @property
    def target(self) -> str:
        """The suggestion as a rule target: region codes become region group ids."""
        if self.suggested in (PROXY, DIRECT, REJECT):
            return self.suggested
        return f"region:{self.suggested}"


CATEGORIES = (
    Category("basic", QT_TRANSLATE_NOOP("Services", "基础")),
    Category("ai", QT_TRANSLATE_NOOP("Services", "AI")),
    Category("streaming", QT_TRANSLATE_NOOP("Services", "流媒体")),
    Category("social", QT_TRANSLATE_NOOP("Services", "社交通讯")),
    Category("games", QT_TRANSLATE_NOOP("Services", "游戏")),
    Category("developer", QT_TRANSLATE_NOOP("Services", "开发")),
    Category("apple", QT_TRANSLATE_NOOP("Services", "Apple")),
    Category("microsoft", QT_TRANSLATE_NOOP("Services", "Microsoft")),
    Category("google", QT_TRANSLATE_NOOP("Services", "Google")),
)


def _site(value: str) -> Match:
    return Match(GEOSITE, value)


def _ip(value: str) -> Match:
    # A domain's request does not resolve its name for an IP rule (routing.md §7).
    return Match(GEOIP, value, no_resolve=True)


def _service(id: str, zh: str, en: str, category: str, suggested: str, *matches: Match) -> Service:
    return Service(id, zh, en, category, matches, suggested)


# In the order the dialog lists them: by category, then roughly by how common they are.
SERVICES = (
    _service(
        "private", "私有网络", "Private networks", "basic", DIRECT, _site("private"), _ip("private")
    ),
    _service("cn", "国内常用", "Mainland China", "basic", DIRECT, _site("cn"), _ip("cn")),
    _service("ads", "广告", "Ads", "basic", REJECT, _site("category-ads-all")),
    _service("openai", "OpenAI", "OpenAI", "ai", "US", _site("openai")),
    _service("anthropic", "Anthropic", "Anthropic", "ai", "US", _site("anthropic")),
    _service("gemini", "Gemini", "Gemini", "ai", "US", _site("google-gemini")),
    _service("perplexity", "Perplexity", "Perplexity", "ai", "US", _site("perplexity")),
    _service("grok", "Grok", "Grok", "ai", "US", _site("xai")),
    _service("netflix", "Netflix", "Netflix", "streaming", PROXY, _site("netflix"), _ip("netflix")),
    _service("youtube", "YouTube", "YouTube", "streaming", PROXY, _site("youtube")),
    _service("disney", "Disney+", "Disney+", "streaming", PROXY, _site("disney")),
    _service("primevideo", "Prime Video", "Prime Video", "streaming", PROXY, _site("primevideo")),
    _service("hbo", "HBO Max", "HBO Max", "streaming", PROXY, _site("hbo")),
    _service("spotify", "Spotify", "Spotify", "streaming", PROXY, _site("spotify")),
    _service("twitch", "Twitch", "Twitch", "streaming", PROXY, _site("twitch")),
    _service("bbc", "BBC iPlayer", "BBC iPlayer", "streaming", "GB", _site("bbc")),
    _service(
        "telegram", "Telegram", "Telegram", "social", PROXY, _site("telegram"), _ip("telegram")
    ),
    _service(
        "twitter",
        "X（Twitter）",  # noqa: RUF001 - Chinese text takes fullwidth brackets
        "X (Twitter)",
        "social",
        PROXY,
        _site("twitter"),
        _ip("twitter"),
    ),
    _service("instagram", "Instagram", "Instagram", "social", PROXY, _site("instagram")),
    _service(
        "facebook", "Facebook", "Facebook", "social", PROXY, _site("facebook"), _ip("facebook")
    ),
    _service("whatsapp", "WhatsApp", "WhatsApp", "social", PROXY, _site("whatsapp")),
    _service("discord", "Discord", "Discord", "social", PROXY, _site("discord")),
    _service("tiktok", "TikTok", "TikTok", "social", PROXY, _site("tiktok")),
    _service("line", "LINE", "LINE", "social", PROXY, _site("line")),
    _service("steam", "Steam", "Steam", "games", PROXY, _site("steam")),
    _service("epicgames", "Epic Games", "Epic Games", "games", PROXY, _site("epicgames")),
    _service("playstation", "PlayStation", "PlayStation", "games", PROXY, _site("playstation")),
    _service("nintendo", "Nintendo", "Nintendo", "games", PROXY, _site("nintendo")),
    _service("xbox", "Xbox", "Xbox", "games", PROXY, _site("xbox")),
    _service("github", "GitHub", "GitHub", "developer", PROXY, _site("github")),
    _service("gitlab", "GitLab", "GitLab", "developer", PROXY, _site("gitlab")),
    _service("docker", "Docker", "Docker", "developer", PROXY, _site("docker")),
    _service("npm", "npm", "npm", "developer", PROXY, _site("npmjs")),
    _service(
        "stackoverflow",
        "Stack Overflow",
        "Stack Overflow",
        "developer",
        PROXY,
        _site("stackexchange"),
    ),
    _service("apple", "Apple 服务", "Apple services", "apple", DIRECT, _site("apple")),
    _service("icloud", "iCloud", "iCloud", "apple", DIRECT, _site("icloud")),
    _service("appletv", "Apple TV+", "Apple TV+", "apple", PROXY, _site("apple-tvplus")),
    _service(
        "microsoft", "Microsoft 服务", "Microsoft services", "microsoft", DIRECT, _site("microsoft")
    ),
    _service("onedrive", "OneDrive", "OneDrive", "microsoft", PROXY, _site("onedrive")),
    _service(
        "google", "Google 服务", "Google services", "google", PROXY, _site("google"), _ip("google")
    ),
    _service("googleplay", "Google Play", "Google Play", "google", PROXY, _site("google-play")),
)

_BY_ID = {service.id: service for service in SERVICES}


def service(service_id: str) -> Service | None:
    """The service with this id, or None when the list no longer has it."""
    return _BY_ID.get(service_id)
