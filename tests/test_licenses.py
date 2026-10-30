"""The third-party license manifest (docs/release.md §5) is current, without needing Go:
upgrading a dependency and forgetting scripts/gen_licenses.py fails here."""

from __future__ import annotations

import json
import platform
import re
from importlib.metadata import distribution
from pathlib import Path

from scripts.gen_licenses import (
    CPYTHON_INPUT,
    QT_DOCS_ONLY,
    QT_INPUT,
    WINTUN_PACKAGE,
    WINTUN_VERSION,
    classify,
    font_version,
    qt_license,
)
from solaris import licenses

ROOT = Path(__file__).resolve().parents[1]
GO_MOD = ROOT / "src" / "core" / "go.mod"
WINTUN = f"Wintun (wintun.dll in {WINTUN_PACKAGE[0]})"


def go_requirements() -> dict[str, str]:
    """Module path to version, from go.mod's require blocks."""
    text = GO_MOD.read_text(encoding="utf-8")
    found = {}
    for block in re.findall(r"require \((.*?)\)", text, re.DOTALL):
        for line in block.splitlines():
            parts = line.split()
            if len(parts) >= 2 and not parts[0].startswith("//"):
                found[parts[0]] = parts[1]
    return found


def notice_count() -> int:
    return len(re.findall(r"^---- \[\d+\] used by: ", licenses.NOTICES.read_text("utf-8"), re.M))


def test_every_component_has_its_texts() -> None:
    components = licenses.load()
    assert len(components) > 100
    count = notice_count()
    for component in components:
        assert component.notices, component.name
        assert all(1 <= n <= count for n in component.notices), component.name
        assert component.license, component.name
        assert component.kind in ("python", "go", "qt", "font")
    data = json.loads(licenses.MANIFEST.read_text("utf-8"))
    used = {n for item in data["components"] for n in item["notices"]}
    assert used == set(range(1, count + 1))  # no text without a component


def test_python_components_match_the_environment() -> None:
    by_name = {c.name: c for c in licenses.load() if c.kind == "python"}
    for name in ("protobuf", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6"):
        assert by_name[name].version == distribution(name).version, name
    major_minor = ".".join(platform.python_version_tuple()[:2])
    assert by_name["CPython"].version.startswith(major_minor + ".")
    assert "PySide6_WebEngine" not in by_name  # never imported, so never shipped


def test_cpython_lists_the_libraries_its_build_bundles() -> None:
    data = json.loads(CPYTHON_INPUT.read_text("utf-8"))
    by_name = {c.name: c for c in licenses.load() if c.kind == "python"}
    cpython = by_name["CPython"]
    assert cpython.version == data["python_version"]
    assert len(cpython.notices) == 2  # its license, and the code incorporated in it
    seen = set()
    for item in data["libraries"]:
        assert item["modules"], item["name"]
        assert item["texts"], item["name"]
        assert set(item["platforms"]) <= {"macOS", "Windows"}, item["name"]
        for platform_ in item["platforms"]:
            assert (item["name"], platform_) not in seen  # one version per platform
            seen.add((item["name"], platform_))
    for name in ("OpenSSL", "SQLite", "libffi", "Expat"):
        assert {(name, "macOS"), (name, "Windows")} <= seen, name
    assert len([name for name in by_name if " (CPython " in name]) == len(data["libraries"])


def test_go_components_match_go_mod() -> None:
    required = go_requirements()
    others = ("Go standard library", WINTUN)
    components = [c for c in licenses.load() if c.kind == "go" and c.name not in others]
    assert any(c.name == "github.com/metacubex/mihomo" for c in components)
    for component in components:
        assert required.get(component.name) == component.version, component.name
    toolchain = re.search(r"^toolchain (go\S+)$", GO_MOD.read_text("utf-8"), re.M)
    assert toolchain is not None
    stdlib = next(c for c in licenses.load() if c.name == "Go standard library")
    assert stdlib.version == toolchain.group(1)


def test_wintun_has_its_own_license() -> None:
    # sing-tun embeds Wintun's prebuilt DLL in the Windows service.
    wintun = next(c for c in licenses.load() if c.name == WINTUN)
    assert wintun.version == WINTUN_VERSION
    assert wintun.license == "LicenseRef-Wintun-Prebuilt-Binaries"
    assert WINTUN_PACKAGE[0] in go_requirements()


def test_qt_matches_pyside() -> None:
    qt = next(c for c in licenses.load() if c.kind == "qt" and c.name == "Qt")
    assert qt.version == distribution("PySide6").version
    assert qt.license == "LGPL-3.0-only"


def test_qt_components_have_their_texts() -> None:
    prefix = QT_DOCS_ONLY.split("{}")[0]
    for item in json.loads(QT_INPUT.read_text("utf-8"))["components"]:
        assert item["texts"], item["name"]
        assert not any(text.startswith(prefix) for text in item["texts"]), item["name"]


def test_fonts_match_the_bundled_files() -> None:
    fonts = {c.name: c for c in licenses.load() if c.kind == "font"}
    directory = ROOT / "src" / "solaris" / "ui" / "fonts"
    assert fonts["Inter"].version == font_version(directory / "inter" / "Inter-Regular.ttf")
    mono = directory / "jetbrains-mono" / "JetBrainsMono-Regular.ttf"
    assert fonts["JetBrains Mono"].version == font_version(mono)
    assert {c.license for c in fonts.values()} == {"OFL-1.1"}


def test_classify_by_title_before_sentences() -> None:
    gpl = (ROOT / "LICENSE").read_text("utf-8")
    assert classify(gpl) == "GPL-3.0-only"  # it names the LGPL further down
    lgpl = (ROOT / "package" / "licenses" / "LGPL-3.0-only.txt").read_text("utf-8")
    assert classify(lgpl) == "LGPL-3.0-only"
    ofl = (ROOT / "src" / "solaris" / "ui" / "fonts" / "inter" / "OFL.txt").read_text("utf-8")
    assert classify(ofl) == "OFL-1.1"  # it opens like the MIT license
    notice = (
        "This program is free software: you can redistribute it and/or modify it under the "
        "terms of the GNU General Public License as published by the Free Software "
        "Foundation, either version 3 of the License, or (at your option) any later version."
    )
    assert classify(notice) == "GPL-3.0-or-later"
    mit = (
        "Permission is hereby granted, free of charge, to any person obtaining a copy ... "
        "The above copyright notice and this permission notice shall be included in all copies"
    )
    assert classify(mit) == "MIT"
    bsd = "Redistribution and use in source and binary forms ... Neither the name of"
    assert classify(bsd) == "BSD-3-Clause"
    assert classify("All rights reserved.") is None


def test_qt_license_wording() -> None:
    assert qt_license('BSD 3-clause "New" or "Revised" License') == "BSD-3-Clause"
    assert qt_license("Freetype Project License or GNU General Public License v2.0 only") == (
        "FTL OR GPL-2.0-only"
    )
    assert qt_license("MIT License and MIT Open Group variant") == "MIT AND MIT-open-group"


def test_a_missing_manifest_lists_nothing(tmp_path: Path) -> None:
    assert licenses.load(tmp_path / "third_party.json") == ()
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert licenses.load(tmp_path / "broken.json") == ()
