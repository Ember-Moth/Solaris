"""The regions of the core's region table (src/core/internal/compose/regions.go), named in the
UI's language. The core only hands the UI region codes (docs/routing.md §8.7); it names the
region groups it generates itself, with a flag, in the plan's locale.

The order is the table's: region groups and the target dropdown list regions in it.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mihomo_desktop.i18n import QT_TRANSLATE_NOOP

OTHER = "ZZ"  # nodes no row of the table matches (CLDR's "unknown region")
REGION_PREFIX = "region:"

REGIONS = {
    "HK": QT_TRANSLATE_NOOP("Regions", "香港"),
    "TW": QT_TRANSLATE_NOOP("Regions", "台湾"),
    "MO": QT_TRANSLATE_NOOP("Regions", "澳门"),
    "JP": QT_TRANSLATE_NOOP("Regions", "日本"),
    "KR": QT_TRANSLATE_NOOP("Regions", "韩国"),
    "SG": QT_TRANSLATE_NOOP("Regions", "新加坡"),
    "US": QT_TRANSLATE_NOOP("Regions", "美国"),
    "CA": QT_TRANSLATE_NOOP("Regions", "加拿大"),
    "GB": QT_TRANSLATE_NOOP("Regions", "英国"),
    "FR": QT_TRANSLATE_NOOP("Regions", "法国"),
    "DE": QT_TRANSLATE_NOOP("Regions", "德国"),
    "NL": QT_TRANSLATE_NOOP("Regions", "荷兰"),
    "RU": QT_TRANSLATE_NOOP("Regions", "俄罗斯"),
    "TR": QT_TRANSLATE_NOOP("Regions", "土耳其"),
    "ID": QT_TRANSLATE_NOOP("Regions", "印度尼西亚"),
    "IN": QT_TRANSLATE_NOOP("Regions", "印度"),
    "TH": QT_TRANSLATE_NOOP("Regions", "泰国"),
    "VN": QT_TRANSLATE_NOOP("Regions", "越南"),
    "MY": QT_TRANSLATE_NOOP("Regions", "马来西亚"),
    "PH": QT_TRANSLATE_NOOP("Regions", "菲律宾"),
    "AU": QT_TRANSLATE_NOOP("Regions", "澳大利亚"),
    "AR": QT_TRANSLATE_NOOP("Regions", "阿根廷"),
    "BR": QT_TRANSLATE_NOOP("Regions", "巴西"),
    "AE": QT_TRANSLATE_NOOP("Regions", "阿联酋"),
    "CN": QT_TRANSLATE_NOOP("Regions", "中国"),
    OTHER: QT_TRANSLATE_NOOP("Regions", "其他地区"),
}
CODES = tuple(REGIONS)


def region_name(code: str) -> str:
    """The region's name in the UI language; the code itself for one the table lacks."""
    text = REGIONS.get(code)
    return QCoreApplication.translate("Regions", text) if text else code


def region_id(code: str) -> str:
    """The id of the region's group, as plans and rule targets write it."""
    return REGION_PREFIX + code


def region_code(group_id: str) -> str | None:
    """The region code of a region group id; None for other ids."""
    return group_id.removeprefix(REGION_PREFIX) if group_id.startswith(REGION_PREFIX) else None
