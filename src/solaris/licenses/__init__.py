"""The third-party license manifest that ships with the app (docs/release.md §5).

``scripts/gen_licenses.py`` writes both files beside this one; packaging copies the
directory to ``solaris/licenses/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
MANIFEST = DIRECTORY / "third_party.json"
NOTICES = DIRECTORY / "THIRD_PARTY_NOTICES.txt"


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    license: str  # an SPDX expression
    kind: str  # "python", "go", "qt" or "font"
    url: str
    notices: tuple[int, ...]  # the numbers of its texts in NOTICES


def load(path: Path = MANIFEST) -> tuple[Component, ...]:
    """The components, in the manifest's order; none when it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            Component(**{**item, "notices": tuple(item["notices"])}) for item in data["components"]
        )
    except OSError, ValueError, KeyError, TypeError:
        return ()
