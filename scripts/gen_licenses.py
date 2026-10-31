"""Write the third-party license manifest that ships with the app (docs/release.md §5).

    uv run python scripts/gen_licenses.py           # regenerate both files
    uv run python scripts/gen_licenses.py --check   # fail when they are stale
    uv run python scripts/gen_licenses.py --qt-docs ~/Qt/Docs/Qt-6.12.0  # refresh qt.json

Inputs: the installed Python packages, the Go modules the Windows service and the macOS
extension link (``go list``, which needs the module cache), ``package/licenses/`` (Qt's
third-party code, read once from Qt's documentation; the libraries CPython's build bundles;
Wintun's license; the LGPL text) and the bundled fonts. Outputs, committed like Theme.qml:
``src/solaris/licenses/third_party.json`` for the settings page and
``THIRD_PARTY_NOTICES.txt`` with every text.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import re
import struct
import subprocess
import sys
import textwrap
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from importlib.metadata import Distribution, distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CORE = ROOT / "src" / "core"
INPUTS = ROOT / "package" / "licenses"
QT_INPUT = INPUTS / "qt.json"
CPYTHON_INPUT = INPUTS / "cpython.json"
WINTUN_LICENSE = INPUTS / "wintun.txt"
LGPL = INPUTS / "LGPL-3.0-only.txt"
OUTPUT = ROOT / "src" / "solaris" / "licenses"
MANIFEST = OUTPUT / "third_party.json"
NOTICES = OUTPUT / "THIRD_PARTY_NOTICES.txt"
FONTS = ROOT / "src" / "solaris" / "ui" / "fonts"
GOPROXY = "https://goproxy.cn,direct"

# Go builds that ship (docs/architecture.md §3.4): the service and the extension's library.
GO_TARGETS = (
    (
        {"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"},
        "./service",
        "with_gvisor,with_naive,with_purego",
    ),
    (
        {"GOOS": "darwin", "GOARCH": "arm64", "CGO_ENABLED": "1"},
        "./apple",
        "with_gvisor,with_naive",
    ),
)
# sing-tun embeds Wintun's prebuilt DLL, which comes under its own license, kept in
# WINTUN_LICENSE from wintun-0.14.1.zip. Another DLL means checking that license again.
WINTUN_PACKAGE = ("github.com/metacubex/sing-tun", "internal/wintun")
WINTUN_DLL = "amd64/wintun.dll"
WINTUN_SHA256 = "e5da8447dc2c320edc0fc52fa01885c103de8c118481f683643cacc3220dafce"
WINTUN_VERSION = "0.14.1"

# PySide6 names these, but the UI never imports them, so Nuitka leaves them out.
NOT_SHIPPED = {"pyside6-webengine", "pyside6-pdf"}
# Licenses the texts or the metadata do not state recognisably.
LICENSE_OVERRIDES: dict[str, str] = {
    # Its LICENSE files sit in the packages; chacha/ itself says "found in the LICENSE file".
    "github.com/metacubex/chacha": "MIT",
    # mihomo's OpenVPN transport. A port of the LZO library, whose license it says it shares:
    # LZO is "version 2 of the License, or (at your option) any later version", so it may be
    # combined with GPL-3.0 code. Ships the GPL-2.0 text only.
    "github.com/rasky/go-lzo": "GPL-2.0-or-later",
}
# Qt's own words for its third-party licenses (licenses-used-in-qt.html), as SPDX.
QT_LICENSE_NAMES = {
    'BSD 3-clause "New" or "Revised" License with PCRE2 binary-like Packages Exception': (
        "BSD-3-Clause WITH PCRE2-exception"
    ),
    'BSD 3-clause "New" or "Revised" Licensee': "BSD-3-Clause",
    'BSD 3-clause "New" or "Revised" License': "BSD-3-Clause",
    'BSD 2-clause "Simplified" License': "BSD-2-Clause",
    "Apache License Version 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "Creative Commons Zero v1.0 Universal": "CC0-1.0",
    "Zlib License": "Zlib",
    "MIT Open Group variant": "MIT-open-group",
    "MIT License": "MIT",
    "Public Domain": "LicenseRef-Public-Domain",
    "Unicode License Agreement - Data Files and Software (2016)": "Unicode-DFS-2016",
    "Unicode License v3": "Unicode-3.0",
    "Freetype Project License": "FTL",
    "GNU General Public License v2.0 only": "GPL-2.0-only",
    "Bitstream Vera Font License": "Bitstream-Vera",
    "Independent JPEG Group License": "IJG",
    "Libpng License": "Libpng",
    "PNG Reference Library version 2": "libpng-2.0",
    "Imlib2 License": "Imlib2",
    "LCS-Telegraphics License": "LicenseRef-LCS-Telegraphics",
    "International Color Consortium License": "LicenseRef-ICC",
    "X11 License": "X11",
    "Historical Permission Notice and Disclaimer": "HPND",
    "Libtiff License": "libtiff",
    "Mozilla Public License 2.0": "MPL-2.0",
}
# The Qt modules the UI ships, as the documentation names their sections.
QT_MODULES = (
    "qt-core",
    "qt-gui",
    "qt-network",
    "qt-qml",
    "qt-quick",
    "qt-quick-controls",
    "qt-image-formats",
)
# What a component says when neither the documentation nor QT_INPUT has its texts.
QT_DOCS_ONLY = "The copyright and license of {} are in Qt's documentation: {}"
# Third-party code of those modules that only Linux, Android or X11 builds contain.
QT_NOT_SHIPPED = re.compile(r"Android|XCB|X Server")

_LICENSE_FILE = re.compile(r"(licen[cs]e|copying|notice|unlicense)([-._][\w.-]*)?", re.IGNORECASE)


@dataclass(frozen=True)
class Entry:
    name: str
    version: str
    license: str
    kind: str  # "python", "go", "qt" or "font"
    url: str
    texts: tuple[str, ...]


# -- recognising a license text --------------------------------------------------------------


def classify(text: str) -> str | None:
    """The SPDX identifier of a license text: by its title when it opens with one, else by
    sentences each license has. Titles first, because licenses quote each other: the GPL
    names the LGPL, the OFL uses the MIT license's opening words."""
    flat = " ".join(text.split())
    head = flat[:600].upper()
    for title, version, identifier in _TITLES:
        if title in head and (not version or version in head):
            # A notice before the GNU text may allow later versions, as sing's does.
            if identifier.endswith("-only") and "ANY LATER VERSION" in head:
                return identifier.removesuffix("-only") + "-or-later"
            return identifier
    if "Redistribution and use in source and binary forms" in flat:
        if "advertising materials" in flat:
            return "BSD-4-Clause"
        if (
            "Neither the name" in flat
            or "names of its contributors" in flat
            or ("may not be used to endorse or promote" in flat)
        ):
            return "BSD-3-Clause"
        return "BSD-2-Clause"
    if "Permission is hereby granted, free of charge" in flat:
        if "The above copyright notice and this permission notice shall be included" in flat:
            return "MIT"
        return "MIT-0"
    if "Permission to use, copy, modify, and/or distribute this software for any purpose" in flat:
        return "ISC"
    if "This is free and unencumbered software released into the public domain" in flat:
        return "Unlicense"
    if "Permission is granted to anyone to use this software for any purpose" in flat:
        return "Zlib"
    return None


# A title near the start of the text, the version it must also name, and the license.
_TITLES = (
    ("GNU LESSER GENERAL PUBLIC LICENSE", "VERSION 3", "LGPL-3.0-only"),
    ("GNU LESSER GENERAL PUBLIC LICENSE", "VERSION 2.1", "LGPL-2.1-only"),
    ("GNU GENERAL PUBLIC LICENSE", "VERSION 3", "GPL-3.0-only"),
    ("GNU GENERAL PUBLIC LICENSE", "VERSION 2", "GPL-2.0-only"),
    ("APACHE LICENSE", "VERSION 2.0", "Apache-2.0"),
    ("MOZILLA PUBLIC LICENSE", "2.0", "MPL-2.0"),
    ("SIL OPEN FONT LICENSE", "1.1", "OFL-1.1"),
    ("BOOST SOFTWARE LICENSE", "1.0", "BSL-1.0"),
    ("CC0 1.0 UNIVERSAL", "", "CC0-1.0"),
    ("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2", "", "PSF-2.0"),
)


def _combine(identifiers: Iterable[str]) -> str:
    unique = list(dict.fromkeys(identifiers))
    return " AND ".join(unique)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()


# -- Python ----------------------------------------------------------------------------------


def _requirements(dist: Distribution) -> Iterator[str]:
    for line in dist.requires or ():
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            yield canonicalize_name(requirement.name)


def _runtime_closure(root: str) -> list[Distribution]:
    seen: dict[str, Distribution] = {}
    queue = list(_requirements(distribution(root)))
    while queue:
        name = queue.pop(0)
        if name in seen or name in NOT_SHIPPED:
            continue
        dist = distribution(name)
        seen[name] = dist
        queue.extend(_requirements(dist))
    return sorted(seen.values(), key=lambda d: canonicalize_name(d.metadata["Name"]))


def _python_texts(dist: Distribution) -> tuple[str, ...]:
    texts = []
    for file in dist.files or ():
        if ".dist-info" in str(file) and _LICENSE_FILE.fullmatch(Path(str(file)).name):
            texts.append(_read(Path(str(dist.locate_file(file)))))
    return tuple(texts)


def _python_url(dist: Distribution) -> str:
    metadata = dist.metadata
    for entry in metadata.get_all("Project-URL") or ():
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository"):
            return url.strip()
    return metadata.get("Home-page") or f"https://pypi.org/project/{metadata['Name']}/"


def python_entries() -> list[Entry]:
    entries = []
    for dist in _runtime_closure("solaris"):
        metadata = dist.metadata
        texts = _python_texts(dist)
        license_ = metadata.get("License-Expression") or ""
        declared = (metadata.get("License") or "").strip()
        if not license_ and declared and "\n" not in declared and len(declared) < 100:
            license_ = declared
        if not license_ or (" " in license_ and not re.search(r" (OR|AND|WITH) ", license_)):
            license_ = _combine(filter(None, map(classify, texts))) or license_
        if license_.startswith("LGPL-3.0-only"):
            texts = texts or (_read(LGPL),)
        if not license_ or not texts:
            raise SystemExit(f"{metadata['Name']}: no license found; add it to the overrides")
        entries.append(
            Entry(metadata["Name"], dist.version, license_, "python", _python_url(dist), texts)
        )
    return entries + _cpython()


def _cpython() -> list[Entry]:
    """Nuitka bundles the interpreter itself, with the libraries its build links into it
    (python-build-standalone, as uv installs it). CPython's own license comes from the
    interpreter; the code incorporated in CPython and the libraries, per module and
    platform, from CPYTHON_INPUT."""
    data = json.loads(CPYTHON_INPUT.read_text(encoding="utf-8"))
    version = platform.python_version()
    if data["python_version"] != version:
        raise SystemExit(
            f"cpython.json describes Python {data['python_version']} but this is {version};"
            " update it from that build's PYTHON.json and licenses/ (docs/release.md §5)"
        )
    base = Path(sys.base_prefix)
    stdlib = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    candidates = (base / "LICENSE.txt", base / "lib" / stdlib / "LICENSE.txt")
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise SystemExit(f"no LICENSE.txt under {base}")
    texts = (_read(path), data["incorporated"])
    entries = [Entry("CPython", version, "PSF-2.0", "python", "https://www.python.org/", texts)]
    for item in data["libraries"]:
        where = ", ".join(item["modules"])
        if len(item["platforms"]) == 1:
            where += f", {item['platforms'][0]}"
        entries.append(
            Entry(
                f"{item['name']} (CPython {where})",
                item["version"],
                item["license"],
                "python",
                item["url"],
                tuple(item["texts"]),
            )
        )
    return entries


# -- Go --------------------------------------------------------------------------------------


@dataclass
class _Module:
    path: str
    version: str
    root: Path
    packages: set[Path]


def _go(*args: str, env: dict[str, str] | None = None) -> str:
    environment = {**os.environ, "GOPROXY": GOPROXY, "GOFLAGS": "-mod=mod", **(env or {})}
    done = subprocess.run(
        ["go", "-C", str(CORE), *args],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"go {' '.join(args)} failed:\n{done.stderr}")
    return done.stdout


def _linked_modules() -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    fields = (
        "{{if .Module.Replace}}{{.Module.Replace.Path}}\t{{.Module.Replace.Version}}"
        "{{else}}{{.Module.Path}}\t{{.Module.Version}}{{end}}\t{{.Module.Dir}}\t{{.Dir}}"
    )
    template = "{{if .Module}}" + fields + "{{end}}"
    for env, target, tags in GO_TARGETS:
        output = _go("list", "-deps", "-tags", tags, "-f", template, target, env=env)
        for line in output.splitlines():
            path, version, root, package = line.split("\t")
            if not version:  # the main module
                continue
            module = modules.setdefault(path, _Module(path, version, Path(root), set()))
            module.packages.add(Path(package))
    # Purego dynamically loads this DLL; it is shipped but absent from go list -deps.
    from scripts import cronet

    native = cronet.library("windows", "amd64")
    modules[native.module] = _Module(native.module, native.version, native.root, {native.root})
    return modules


def _license_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and _LICENSE_FILE.fullmatch(path.name) and path.suffix != ".go"
    )


def _module_texts(module: _Module) -> list[Path]:
    """The module's own license files; failing those, the nearest ones above the packages
    that are linked."""
    found = _license_files(module.root)
    if found:
        return found
    for package in sorted(module.packages):
        directory = package
        while directory != module.root and module.root in directory.parents:
            if files := _license_files(directory):
                found += [f for f in files if f not in found]
                break
            directory = directory.parent
    return found


def _go_url(path: str) -> str:
    parts = path.split("/")
    if parts[0] in ("github.com", "gitlab.com", "codeberg.org") and len(parts) >= 3:
        return "https://" + "/".join(parts[:3])
    return f"https://pkg.go.dev/{path}"


def go_entries() -> list[Entry]:
    entries = []
    for module in sorted(_linked_modules().values(), key=lambda m: m.path.lower()):
        files = _module_texts(module)
        if not files:
            raise SystemExit(f"{module.path}: no license file; add it to the overrides")
        texts = tuple(_read(path) for path in files)
        licenses = [classify(_read(p)) for p in files if not p.name.lower().startswith("notice")]
        license_ = LICENSE_OVERRIDES.get(module.path) or _combine(filter(None, licenses))
        if not license_ or (None in licenses and module.path not in LICENSE_OVERRIDES):
            raise SystemExit(f"{module.path}: cannot tell its license; add it to the overrides")
        entries.append(
            Entry(module.path, module.version, license_, "go", _go_url(module.path), texts)
        )
        if module.path == WINTUN_PACKAGE[0]:
            entries += _wintun(module)
    version = _go("env", "GOVERSION").strip()
    goroot = Path(_go("env", "GOROOT").strip())
    # Homebrew moves LICENSE out of GOROOT, one level up.
    license_file = next(p for p in (goroot / "LICENSE", goroot.parent / "LICENSE") if p.is_file())
    texts = (_read(license_file), *(_read(p) for p in [goroot / "PATENTS"] if p.is_file()))
    entries.append(
        Entry("Go standard library", version, "BSD-3-Clause", "go", "https://go.dev/", texts)
    )
    return entries


def _wintun(module: _Module) -> list[Entry]:
    """The Wintun DLL that sing-tun embeds, when the Windows service links that package."""
    package = module.root / WINTUN_PACKAGE[1]
    if package not in module.packages:
        return []
    digest = hashlib.sha256((package / WINTUN_DLL).read_bytes()).hexdigest()
    if digest != WINTUN_SHA256:
        raise SystemExit(
            f"{module.path} embeds another {WINTUN_DLL} ({digest}); check Wintun's license again"
        )
    return [
        Entry(
            f"Wintun (wintun.dll in {module.path})",
            WINTUN_VERSION,
            "LicenseRef-Wintun-Prebuilt-Binaries",
            "go",
            "https://www.wintun.net/",
            (_read(WINTUN_LICENSE),),
        )
    ]


# -- Qt --------------------------------------------------------------------------------------


def qt_license(name: str) -> str:
    """Qt's license line, such as 'Freetype Project License or GNU General Public License
    v2.0 only', as an SPDX expression."""
    expression = name.strip().rstrip(".")
    for words in sorted(QT_LICENSE_NAMES, key=len, reverse=True):
        pattern = re.compile(re.escape(words), re.IGNORECASE)
        expression = pattern.sub(f"\0{QT_LICENSE_NAMES[words]}\0", expression)
    parts = [part.strip() for part in expression.split("\0") if part.strip()]
    spdx = []
    for n, part in enumerate(parts):
        if n % 2 == 0:
            if part not in QT_LICENSE_NAMES.values():
                raise SystemExit(f"unknown Qt license {part!r} in {name!r}")
            spdx.append(part)
        elif part.lower() in ("or",):
            spdx.append("OR")
        elif part.lower() in ("and", ", and", ","):
            spdx.append("AND")
        else:
            raise SystemExit(f"unknown Qt license wording {part!r} in {name!r}")
    return " ".join(spdx)


def _pre_blocks(page: str) -> list[str]:
    blocks = []
    for match in re.finditer(r"<pre[^>]*>(.*?)</pre>", page, re.DOTALL):
        text = html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))
        blocks.append(textwrap.dedent(text).strip())
    return [block for block in blocks if block]


def read_qt_docs(docs: Path) -> dict[str, object]:
    """Qt's third-party code in the modules that ship, from the documentation of the Qt
    version PySide6 is built on. A component whose page is missing keeps the texts that
    QT_INPUT has for the same version (added by hand from Qt's sources)."""
    stored = {}
    if QT_INPUT.is_file():
        for item in json.loads(QT_INPUT.read_text(encoding="utf-8"))["components"]:
            stored[item["name"], item["version"]] = item
    page = (docs / "qtdoc" / "licenses-used-in-qt.html").read_text(encoding="utf-8")
    title = re.search(r"Qt (\d+\.\d+\.\d+) Reference Documentation", page)
    if title is None:
        raise SystemExit("no Qt version in licenses-used-in-qt.html")
    components = []
    sections = re.split(r'<h2 id="([\w-]+)">', page)
    for section_id, body in zip(sections[1::2], sections[2::2], strict=True):
        if section_id not in QT_MODULES:
            continue
        module = re.sub(r"<[^>]+>", "", body.split("</h2>", 1)[0]).strip()
        rows = re.findall(
            r'<a href="\.\./([^"]+)">([^<]+)</a></p></td><td class="tblDescr"><p>([^<]+)</p>',
            body,
        )
        for href, label, license_name in rows:
            label = html.unescape(label)
            if QT_NOT_SHIPPED.search(label):
                continue
            name, _, version = label.partition(", version ")
            online = f"https://doc.qt.io/qt-6/{Path(href).name}"
            page_path = docs / href
            known = stored.get((f"{name} ({module})", version))
            if page_path.is_file():
                attribution = page_path.read_text(encoding="utf-8")
                homepage = re.search(r'<a href="([^"]+)">Project Homepage</a>', attribution)
                text = "\n\n".join(_pre_blocks(attribution))
            elif known is not None and known["texts"] != [QT_DOCS_ONLY.format(label, online)]:
                components.append(known)
                continue
            else:  # a module whose documentation is not installed
                print(f"warning: no {href}; pointing to {online}", file=sys.stderr)
                homepage = None
                text = QT_DOCS_ONLY.format(label, online)
            components.append(
                {
                    "name": f"{name} ({module})",
                    "version": version,
                    "license": qt_license(html.unescape(license_name)),
                    "url": homepage.group(1) if homepage else online,
                    "texts": [text],
                }
            )
    return {"qt_version": title.group(1), "components": components}


def qt_entries() -> list[Entry]:
    data = json.loads(QT_INPUT.read_text(encoding="utf-8"))
    version = data["qt_version"]
    pyside = distribution("pyside6").version
    if version != pyside:
        raise SystemExit(
            f"qt.json describes Qt {version} but PySide6 is {pyside}; rerun with --qt-docs"
        )
    entries = [
        Entry(
            "Qt",
            version,
            "LGPL-3.0-only",
            "qt",
            f"https://download.qt.io/official_releases/qt/{version.rsplit('.', 1)[0]}/{version}/",
            (_read(LGPL),),
        )
    ]
    for item in data["components"]:
        texts = tuple(text for text in item["texts"] if text)
        entries.append(
            Entry(item["name"], item["version"], item["license"], "qt", item["url"], texts)
        )
    return entries


# -- fonts -----------------------------------------------------------------------------------


def font_version(path: Path) -> str:
    """The version string in a TrueType font's name table (name ID 5)."""
    data = path.read_bytes()
    (tables,) = struct.unpack_from(">H", data, 4)
    for n in range(tables):
        tag, _, offset, _ = struct.unpack_from(">4sIII", data, 12 + 16 * n)
        if tag != b"name":
            continue
        _, count, strings = struct.unpack_from(">HHH", data, offset)
        for r in range(count):
            platform_id, _, _, name_id, length, start = struct.unpack_from(
                ">HHHHHH", data, offset + 6 + 12 * r
            )
            if name_id != 5:
                continue
            raw = data[offset + strings + start : offset + strings + start + length]
            text = raw.decode("utf-16-be" if platform_id in (0, 3) else "mac-roman")
            return text.removeprefix("Version ").split(";")[0].strip()
    raise SystemExit(f"{path}: no version in its name table")


FONT_HOMES = {
    "inter": ("Inter", "https://github.com/rsms/inter"),
    "jetbrains-mono": ("JetBrains Mono", "https://github.com/JetBrains/JetBrainsMono"),
}


def font_entries() -> list[Entry]:
    entries = []
    for directory in sorted(p for p in FONTS.iterdir() if p.is_dir()):
        name, url = FONT_HOMES[directory.name]
        font = sorted(directory.glob("*.ttf"))[0]
        text = _read(directory / "OFL.txt")
        license_ = classify(text)
        if license_ is None:
            raise SystemExit(f"{directory}: cannot tell the font's license")
        entries.append(Entry(name, font_version(font), license_, "font", url, (text,)))
    return entries


# -- output ----------------------------------------------------------------------------------

HEADER = """\
Solaris third-party notices
===========================

Solaris is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License version 3 (GPL-3.0-only). Its full text is in the LICENSE file
that comes with the program.

Solaris includes the third-party components listed below, each under its own license; the
license texts follow the list. Texts that several components share are given once.

Corresponding source code:
- Solaris, mihomo and every Go module: each release on
  https://github.com/Ember-Moth/Solaris/releases has Solaris-<version>-source.tar.gz, the
  tagged source with all Go dependencies vendored.
- Qt {qt} and PySide6 {qt}: https://download.qt.io/official_releases/qt/ and
  https://download.qt.io/official_releases/QtForPython/ for the same version.
"""

KIND_TITLES = {"python": "Python", "go": "Go", "qt": "Qt", "font": "Fonts"}


def render(entries: list[Entry]) -> tuple[str, str]:
    texts: list[str] = []
    numbers: dict[str, int] = {}
    users: dict[int, list[str]] = {}
    components = []
    for entry in entries:
        indices = []
        for text in entry.texts:
            if text not in numbers:
                texts.append(text)
                numbers[text] = len(texts)
            indices.append(numbers[text])
            users.setdefault(numbers[text], []).append(f"{entry.name} {entry.version}".strip())
        components.append(
            {
                "name": entry.name,
                "version": entry.version,
                "license": entry.license,
                "kind": entry.kind,
                "url": entry.url,
                "notices": indices,
            }
        )
    manifest = json.dumps(
        {"generated_by": "scripts/gen_licenses.py", "components": components},
        ensure_ascii=False,
        indent=2,
    )
    qt = next(e.version for e in entries if e.kind == "qt" and e.name == "Qt")
    lines = [HEADER.format(qt=qt)]
    for kind, title in KIND_TITLES.items():
        lines += ["", title, "-" * len(title), ""]
        for entry, component in zip(entries, components, strict=True):
            if entry.kind == kind:
                refs = ", ".join(f"[{n}]" for n in component["notices"])
                title = f"{entry.name} {entry.version}".strip()
                lines.append(f"{title} -- {entry.license} -- {entry.url} {refs}")
    lines += ["", "", "License texts", "=============", ""]
    for number, text in enumerate(texts, start=1):
        lines += [f"---- [{number}] used by: {', '.join(users[number])}", "", text, ""]
    return manifest + "\n", "\n".join(lines).rstrip() + "\n"


def generate() -> tuple[str, str]:
    entries = python_entries() + go_entries() + qt_entries() + font_entries()
    return render(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--check", action="store_true", help="fail when the files are stale")
    parser.add_argument("--qt-docs", type=Path, help="Qt's documentation, to refresh qt.json")
    args = parser.parse_args()
    if args.qt_docs:
        data = read_qt_docs(args.qt_docs.expanduser())
        QT_INPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest, notices = generate()
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, text in ((MANIFEST, manifest), (NOTICES, notices))
            if not path.is_file() or path.read_text(encoding="utf-8") != text
        ]
        for path in stale:
            print(f"{path} is stale; run scripts/gen_licenses.py", file=sys.stderr)
        return 1 if stale else 0
    MANIFEST.write_text(manifest, encoding="utf-8", newline="\n")
    NOTICES.write_text(notices, encoding="utf-8", newline="\n")
    print(f"{len(json.loads(manifest)['components'])} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
