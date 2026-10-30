"""The service list and the region names the routing page shows (docs/routing.md §7, §8.7)."""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from solaris.host import geo_categories
from solaris.models import services
from solaris.models.regions import CODES, OTHER, REGIONS
from solaris.models.services import CATEGORIES, GEOIP, GEOSITE, SERVICES

ROOT = Path(__file__).resolve().parents[1]
REGIONS_GO = ROOT / "src" / "core" / "internal" / "compose" / "regions.go"
EN_TS = ROOT / "src" / "solaris" / "translations" / "en.ts"


def test_every_category_named_is_in_the_shipped_geo_data() -> None:
    """The installers' GEO data has every category the list expands to (routing.md §7)."""
    known = {GEOSITE: set(geo_categories.GEOSITE), GEOIP: set(geo_categories.GEOIP)}
    missing = [
        (service.id, match.type, match.value)
        for service in SERVICES
        for match in service.matches
        if match.value not in known[match.type]
    ]
    assert missing == []


def test_the_list_has_about_forty_single_services_in_every_category() -> None:
    assert 35 <= len(SERVICES) <= 45
    ids = [service.id for service in SERVICES]
    assert len(set(ids)) == len(ids)
    counts = Counter(service.category for service in SERVICES)
    assert set(counts) == {category.id for category in CATEGORIES}
    # The basics, and no bundle that would overlap single services.
    basic = [s.id for s in SERVICES if s.category == "basic"]
    assert basic == ["private", "cn", "ads"]
    assert not any(service.id in ("ai", "streaming", "media") for service in SERVICES)
    # Listed by category, in the categories' order.
    order = [category.id for category in CATEGORIES]
    assert [service.category for service in SERVICES] == sorted(
        (service.category for service in SERVICES), key=order.index
    )


def test_ip_categories_do_not_resolve_names() -> None:
    for service in SERVICES:
        for match in service.matches:
            assert match.no_resolve == (match.type == GEOIP), service.id
            assert match.type in (GEOSITE, GEOIP)
    telegram = services.service("telegram")
    assert telegram is not None
    assert [m.item() for m in telegram.matches] == [
        {"type": "GEOSITE", "value": "telegram"},
        {"type": "GEOIP", "value": "telegram", "no_resolve": True},
    ]


def test_suggested_targets_are_regions_or_outbounds() -> None:
    for service in SERVICES:
        assert service.suggested in (*CODES, services.PROXY, services.DIRECT, services.REJECT)
        assert service.name_zh
        assert service.name_en
    openai, ads = services.service("openai"), services.service("ads")
    assert openai is not None
    assert ads is not None
    assert (openai.target, ads.target) == ("region:US", "REJECT")
    assert services.service("gone") is None


def test_regions_follow_the_core_table() -> None:
    """Same codes in the same order as regions.go; the Chinese names are the core's, and
    en.ts gives the core's English ones."""
    rows = re.findall(
        r'Code: (?:"([A-Z]{2})"|OtherRegion), Flag: "[^"]*", NameZH: "([^"]+)", NameEN: "([^"]+)"',
        REGIONS_GO.read_text(encoding="utf-8"),
    )
    table = [(code or OTHER, zh, en) for code, zh, en in rows]
    assert [code for code, _, _ in table] == list(CODES)
    assert [(code, REGIONS[code]) for code in CODES] == [(code, zh) for code, zh, _ in table]
    english = {}
    for context in ET.parse(EN_TS).getroot():
        if context.findtext("name") == "Regions":
            for message in context.findall("message"):
                english[message.findtext("source")] = message.findtext("translation")
    assert [english[REGIONS[code]] for code in CODES] == [en for _, _, en in table]
