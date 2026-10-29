"""Lint the QML with PySide6's qmllint.

    uv run python scripts/qmllint.py  # exit 1 on any finding that qmllint_exemptions.toml
                                      # does not list, or on an exemption that matched nothing

The ViewModels register themselves from Python as the MihomoDesktop module, so qmllint
cannot read their types from a plugin. They are described for it instead, as pyside6-project
does: pyside6-metaobjectdump reads the classes from the source and pyside6-qmltyperegistrar
turns that into a .qmltypes file, in a temporary MihomoDesktop module on the import path.

qmllint runs with its default categories. Every finding counts, info included; the few that
cannot be fixed are listed one by one, with the reason, in qmllint_exemptions.toml. Findings
are read from qmllint's JSON: its plain output prints a message once even when several files
share it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "mihomo_desktop"
UI = PACKAGE / "ui"
VIEWMODELS = PACKAGE / "viewmodels"
EXEMPTIONS = Path(__file__).with_name("qmllint_exemptions.toml")
MODULE = "MihomoDesktop"


@dataclass(frozen=True)
class Finding:
    file: str  # relative to the ui directory, with forward slashes
    line: int
    column: int
    category: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.column}: [{self.category}] {self.message}"


@dataclass(frozen=True)
class Exemption:
    file: str
    category: str
    message: str  # a substring of the finding's message
    reason: str

    def matches(self, finding: Finding) -> bool:
        return (
            finding.file == self.file
            and finding.category == self.category
            and self.message in finding.message
        )


def tool(name: str) -> str:
    """A PySide6 wrapper such as pyside6-qmllint, from PATH or beside this interpreter."""
    found = shutil.which(name) or shutil.which(name, path=str(Path(sys.executable).parent))
    if found is None:
        raise SystemExit(f"{name} not found; run `uv sync`")
    return found


def qt_metatypes() -> Path:
    """Qt's own metatypes, which the ViewModels' base classes come from."""
    import PySide6

    package = Path(PySide6.__file__).resolve().parent
    return package / "metatypes" if sys.platform == "win32" else package / "Qt" / "metatypes"


def qml_sources() -> list[Path]:
    """The Python files that register types in the MihomoDesktop module."""
    return sorted(
        path
        for path in VIEWMODELS.rglob("*.py")
        if f'QML_IMPORT_NAME = "{MODULE}"' in path.read_text(encoding="utf-8")
    )


def describe_module(imports: Path) -> None:
    """Write the MihomoDesktop module's qmldir and .qmltypes under ``imports``."""
    module = imports / MODULE
    module.mkdir(parents=True)
    metatypes = imports / "metatypes.json"
    dump = [tool("pyside6-metaobjectdump"), "--quiet", "-o", str(metatypes)]
    subprocess.run([*dump, *map(str, qml_sources())], check=True)
    base = qt_metatypes()
    foreign = ",".join(str(base / f"qt6{name}_metatypes.json") for name in ("core", "qml"))
    register = [
        tool("pyside6-qmltyperegistrar"),
        "--import-name",
        MODULE,
        "--major-version",
        "1",
        "--minor-version",
        "0",
        f"--foreign-types={foreign}",
        "--generate-qmltypes",
        str(module / "plugin.qmltypes"),
        "-o",
        str(imports / "registrations.cpp"),
        str(metatypes),
    ]
    subprocess.run(register, check=True, capture_output=True)
    qmldir = f"module {MODULE}\ntypeinfo plugin.qmltypes\ndepends QtQml\n"
    (module / "qmldir").write_text(qmldir, encoding="utf-8")


def qml_files() -> list[str]:
    return sorted(path.relative_to(UI).as_posix() for path in UI.rglob("*.qml"))


def lint() -> list[Finding]:
    """Everything qmllint reports on the UI."""
    with tempfile.TemporaryDirectory(prefix="mihomo-desktop-qmllint-") as directory:
        imports = Path(directory)
        describe_module(imports)
        command = [tool("pyside6-qmllint"), "--ignore-settings", "-I", str(imports)]
        command += ["--json", "-", *qml_files()]
        # The exit status only says whether there were findings; the JSON lists them.
        result = subprocess.run(command, cwd=UI, capture_output=True, text=True, check=False)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"qmllint failed:\n{result.stdout}{result.stderr}") from None
    return [
        Finding(
            file=Path(entry["filename"]).as_posix(),
            line=warning.get("line", 0),
            column=warning.get("column", 0),
            category=warning.get("id", ""),
            message=warning["message"],
        )
        for entry in report["files"]
        for warning in entry["warnings"]
    ]


def exemptions() -> list[Exemption]:
    with EXEMPTIONS.open("rb") as file:
        entries = tomllib.load(file)["exemption"]
    return [Exemption(**entry) for entry in entries]


def check() -> tuple[list[Finding], list[Exemption]]:
    """The findings no exemption covers, and the exemptions that covered nothing."""
    findings, allowed = lint(), exemptions()
    unexpected = [f for f in findings if not any(e.matches(f) for e in allowed)]
    stale = [e for e in allowed if not any(e.matches(f) for f in findings)]
    return unexpected, stale


def main() -> int:
    unexpected, stale = check()
    for finding in unexpected:
        print(finding)
    for exemption in stale:
        print(f"stale exemption: {exemption.file} [{exemption.category}] {exemption.message}")
    return 1 if unexpected or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
