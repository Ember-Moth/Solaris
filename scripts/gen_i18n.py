"""Extract the UI strings into the translation catalogs and compile them.

    uv run python scripts/gen_i18n.py          # update the .ts files, compile the .qm files
    uv run python scripts/gen_i18n.py --check  # exit 1 if a catalog is stale or unfinished

Source strings are Simplified Chinese, in qsTr() and QCoreApplication.translate()/self.tr()
calls. Translate new entries in src/solaris/translations/<language>.ts, by hand or with
pyside6-linguist, then run this again. The .qm files are committed so the app runs from a
checkout without this step.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "solaris"
TRANSLATIONS = PACKAGE / "translations"
SOURCE_LANGUAGE = "zh_CN"
LANGUAGES = ("en",)
# %n, %1 to %99, and str.format fields such as {core}.
PLACEHOLDER = re.compile(r"%n|%\d+|\{[a-z_]*\}")


def sources() -> list[Path]:
    files = sorted(PACKAGE.rglob("*.qml")) + sorted(PACKAGE.rglob("*.py"))
    return [path for path in files if path.parent.name != "protocol"]


def tool(name: str) -> str:
    """A PySide6 wrapper such as pyside6-lupdate, from PATH or beside this interpreter."""
    found = shutil.which(name) or shutil.which(name, path=str(Path(sys.executable).parent))
    if found is None:
        raise SystemExit(f"{name} not found; run `uv sync`")
    return found


def lupdate(ts: Path, language: str) -> None:
    # Locations are left out so that moving code does not change the catalog.
    args = ["-source-language", SOURCE_LANGUAGE, "-target-language", language]
    args += ["-locations", "none", "-no-obsolete", "-silent"]
    files = [str(path) for path in sources()]
    subprocess.run([tool("pyside6-lupdate"), *files, *args, "-ts", str(ts)], check=True)


def lrelease(ts: Path, qm: Path) -> None:
    command = [tool("pyside6-lrelease"), "-nounfinished", "-silent", str(ts), "-qm", str(qm)]
    subprocess.run(command, check=True)


def catalog(language: str) -> Path:
    return TRANSLATIONS / f"{language}.ts"


def binary(language: str) -> Path:
    return TRANSLATIONS / f"{language}.qm"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def stale_catalogs() -> list[str]:
    """Catalogs that lupdate would change: a string was added, edited or removed."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="solaris-i18n-") as directory:
        for language in LANGUAGES:
            current = catalog(language)
            updated = Path(directory) / current.name
            if current.exists():
                shutil.copyfile(current, updated)
            lupdate(updated, language)
            if not current.exists() or _text(current) != _text(updated):
                problems.append(f"{current.relative_to(ROOT)} is stale; run scripts/gen_i18n.py")
    return problems


def _messages(language: str) -> list[tuple[str, str, ET.Element]]:
    root = ET.parse(catalog(language)).getroot()
    return [
        (context.findtext("name", ""), message.findtext("source", ""), message)
        for context in root.iter("context")
        for message in context.iter("message")
    ]


def unfinished_messages() -> list[str]:
    """Entries without a finished translation, and translations whose placeholders differ
    from the source's."""
    problems = []
    for language in LANGUAGES:
        for context, source, message in _messages(language):
            where = f"{language}: {context}: {source!r}"
            translation = message.find("translation")
            if translation is None or translation.get("type") in ("unfinished", "vanished"):
                problems.append(f"{where} is untranslated")
                continue
            forms = translation.findall("numerusform") if message.get("numerus") == "yes" else []
            texts = [form.text or "" for form in forms] or [translation.text or ""]
            if any(not text.strip() for text in texts):
                problems.append(f"{where} has an empty translation")
            expected = sorted(PLACEHOLDER.findall(source))
            if any(sorted(PLACEHOLDER.findall(text)) != expected for text in texts):
                problems.append(f"{where}: placeholders differ from the source")
    return problems


def stale_binaries() -> list[str]:
    """Compiled catalogs that do not match their .ts."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="solaris-i18n-") as directory:
        for language in LANGUAGES:
            compiled = Path(directory) / f"{language}.qm"
            lrelease(catalog(language), compiled)
            current = binary(language)
            if not current.exists() or current.read_bytes() != compiled.read_bytes():
                problems.append(f"{current.relative_to(ROOT)} is stale; run scripts/gen_i18n.py")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Update and compile the translation catalogs")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        problems = stale_catalogs() or unfinished_messages() + stale_binaries()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    TRANSLATIONS.mkdir(parents=True, exist_ok=True)
    for language in LANGUAGES:
        lupdate(catalog(language), language)
        lrelease(catalog(language), binary(language))
        print(f"wrote {catalog(language).relative_to(ROOT)}, {binary(language).relative_to(ROOT)}")
    if problems := unfinished_messages():
        print("\n".join(problems), file=sys.stderr)
        print("translate these entries, then run this again", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
