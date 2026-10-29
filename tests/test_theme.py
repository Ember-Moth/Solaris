import pytest

from scripts.gen_theme import DESIGN_MD, THEME_QML, color_modes, frontmatter, render

TOKENS = frontmatter(DESIGN_MD.read_text(encoding="utf-8"))
LIGHT, DARK = color_modes(TOKENS["colors"])

# Where DESIGN.md puts each foreground, and the WCAG AA ratio it needs there: 4.5 for text,
# 3 for icons, lines, rings and control outlines.
ROWS = ["background", "surface", "surface-subtle", "surface-selected"]
PAIRS = [
    *[
        (text, ROWS, 4.5)
        for text in [
            "text",
            "text-secondary",
            "text-tertiary",
            "text-muted",
            "link",
            "info",
            "success",
            "warning",
            "latency-poor",
            "danger",
        ]
    ],
    ("text", ["sidebar", "sidebar-hover"], 4.5),  # nav items
    ("text-secondary", ["sidebar", "sidebar-hover"], 4.5),
    ("text-muted", ["sidebar"], 4.5),  # the sidebar's captions
    ("text-tertiary", ["control-track"], 4.5),  # unselected segments
    ("primary", ["surface"], 4.5),  # rate-download-glyph
    ("chart-upload", ["surface"], 4.5),  # rate-upload-glyph
    ("on-primary", ["primary", "primary-hover", "primary-pressed"], 4.5),
    ("primary", ["background", "sidebar", "surface", "surface-selected"], 3),
    ("border-control", ["background", "surface", "surface-selected"], 3),
    ("surface", ["border-control", "primary"], 3),  # the switch thumb on its track
]


def luminance(color: str) -> float:
    def channel(i: int) -> float:
        c = int(color[i : i + 2], 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_theme_qml_is_generated_from_design_md() -> None:
    assert THEME_QML.read_text(encoding="utf-8") == render(TOKENS)


def test_every_color_role_has_both_values() -> None:
    assert list(LIGHT) == list(DARK)


def test_a_role_without_a_dark_value_is_refused() -> None:
    colors = {"text": "#000000", "dark": {}}
    with pytest.raises(SystemExit, match=r"colors\.dark\.text"):
        color_modes(colors)


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
def test_contrast_meets_design_md(palette: dict[str, str]) -> None:
    failures = [
        f"{fg} on {bg}: {contrast(palette[fg], palette[bg]):.2f} < {minimum}"
        for fg, backgrounds, minimum in PAIRS
        for bg in backgrounds
        if contrast(palette[fg], palette[bg]) < minimum
    ]
    assert failures == []


def test_dark_backgrounds_are_not_black_and_rise_with_elevation() -> None:
    layers = ["control-track", "background", "sidebar", "surface", "surface-selected"]
    levels = [luminance(DARK[name]) for name in layers]
    assert levels == sorted(levels)
    assert all(DARK[name] != "#000000" for name in layers)
