"""Generate src/mihomo_desktop/ui/components/Theme.qml from the DESIGN.md frontmatter.

    uv run python scripts/gen_theme.py          # write
    uv run python scripts/gen_theme.py --check  # exit 1 if the file is stale

DESIGN.md is the visual source of truth; Theme.qml is a build artefact that is
committed so the app runs without this step.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DESIGN_MD = ROOT / "DESIGN.md"
THEME_QML = ROOT / "src" / "mihomo_desktop" / "ui" / "components" / "Theme.qml"

HEADER = "// GENERATED from DESIGN.md by scripts/gen_theme.py. Do not edit; edit DESIGN.md.\n"


def frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        sys.exit("DESIGN.md has no YAML frontmatter")
    return yaml.safe_load(match.group(1))


def camel(name: str) -> str:
    head, *tail = re.split(r"[-_]", str(name))
    result = head + "".join(p[:1].upper() + p[1:] for p in tail)
    # QML treats `onX` as a signal handler, so `on-primary` becomes `textOnPrimary`.
    if re.match(r"on[A-Z]", result):
        result = "textO" + result[1:]
    return result


def px(value: str | int | float) -> str:
    text = str(value).strip()
    m = re.fullmatch(r"([\d.]+)px", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\d.]+", text):
        return text
    sys.exit(f"unsupported dimension: {value!r}")


def render(tokens: dict[str, Any]) -> str:
    out = [HEADER, "pragma Singleton", "import QtQuick", "", "QtObject {"]

    out.append("    // ---- colors ----")
    for name, value in tokens["colors"].items():
        out.append(f'    readonly property color {camel(name)}: "{value}"  // {name}')

    out.append("")
    out.append(
        "    // ---- typography (fontSize px, fontWeight 100-900, lineHeight multiplier) ----"
    )
    out.append("    component TypeRole: QtObject {")
    out.append("        property string fontFamily")
    out.append("        property int fontSize")
    out.append("        property int fontWeight")
    out.append("        property real lineHeight: 1.0")
    out.append("    }")
    out.append("    component TypeScale: QtObject {")
    for name, spec in tokens["typography"].items():
        out.append(f"        readonly property TypeRole {camel(name)}: TypeRole {{  // {name}")
        out.append(f'            fontFamily: "{spec["fontFamily"]}"')
        out.append(f"            fontSize: {px(spec['fontSize'])}")
        out.append(f"            fontWeight: {int(spec['fontWeight'])}")
        out.append(f"            lineHeight: {float(spec.get('lineHeight', 1.0))}")
        out.append("        }")
    out.append("    }")
    out.append("    readonly property TypeScale type: TypeScale {}")

    out.append("")
    out.append("    // ---- rounded ----")
    for name, value in tokens["rounded"].items():
        prop = "radius" + camel(name)[:1].upper() + camel(name)[1:]
        out.append(f"    readonly property int {prop}: {px(value)}  // rounded.{name}")

    out.append("")
    out.append("    // ---- spacing ----")
    for name, value in tokens["spacing"].items():
        prop = camel(name)
        if prop[:1].isdigit():
            prop = f"space{prop}"
        out.append(f"    readonly property int {prop}: {px(value)}  // spacing.{name}")

    out.append("}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Theme.qml from DESIGN.md")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    generated = render(frontmatter(DESIGN_MD.read_text(encoding="utf-8")))
    if args.check:
        current = THEME_QML.read_text(encoding="utf-8") if THEME_QML.exists() else ""
        if current != generated:
            print(
                f"{THEME_QML.relative_to(ROOT)} is stale; run scripts/gen_theme.py", file=sys.stderr
            )
            return 1
        return 0
    THEME_QML.parent.mkdir(parents=True, exist_ok=True)
    THEME_QML.write_text(generated, encoding="utf-8")
    print(f"wrote {THEME_QML.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
