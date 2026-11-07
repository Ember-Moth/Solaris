"""The draft release's assets and where its source is (docs/release.md §1, §5, D43): the
three installers the updater looks for and SHA256SUMS, no source archive, and the notes
point at the tag, the mihomo commit go.mod pins and the other Go modules."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from solaris import licenses
from solaris.update.releases import CHECKSUMS_NAME, installer_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
NOTES = ROOT / ".github" / "release-notes.md"
GO_MOD = ROOT / "src" / "core" / "go.mod"
MIHOMO = "github.com/Ember-Moth/mihomo"


def release_job() -> dict[str, Any]:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["jobs"]["release"]


def step(name: str) -> dict[str, Any]:
    (found,) = [s for s in release_job()["steps"] if s.get("name") == name]
    return found


def command(run: str, program: str) -> list[str]:
    """The words of the shell command in ``run`` that starts with ``program``."""
    joined = run.replace("\\\n", " ")
    (line,) = [line for line in joined.splitlines() if line.strip().startswith(program)]
    return shlex.split(line)


def outputs(version: str) -> dict[str, str]:
    """The Version step's outputs for a tag of ``version``."""
    run = step("Version")["run"]
    found = dict(re.findall(r'echo "(\w+)=([^"]*)"', run))
    return {key: value.replace("$version", version) for key, value in found.items()}


def test_the_draft_carries_the_three_installers_and_their_checksums() -> None:
    values = outputs("1.2.3")
    assert values == {
        "version": "1.2.3",
        "dmg": installer_name("1.2.3", "darwin", "arm64"),
        "installer": installer_name("1.2.3", "win32", "x64"),
        "installer_arm64": installer_name("1.2.3", "win32", "arm64"),
    }
    assert installer_name("1.2.3", "darwin", "x64") == values["dmg"]
    installers = ["$DMG", "$INSTALLER", "$INSTALLER_ARM64"]
    assert command(step("SHA256SUMS")["run"], "sha256sum") == [
        "sha256sum",
        *installers,
        ">",
        CHECKSUMS_NAME,
    ]
    create = command(step("Create the draft")["run"], "gh release create")
    assert [word for word in create if word.startswith("build/")] == [
        *(f"build/release/{name}" for name in installers),
        f"build/release/{CHECKSUMS_NAME}",
    ]


def test_the_release_job_builds_no_source_archive() -> None:
    job = release_job()
    text = yaml.safe_dump(job)
    assert "tar.gz" not in text
    assert "go mod vendor" not in text
    assert not [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-go")]
    # Every output a step reads is one the Version step writes.
    used = set(re.findall(r"steps\.version\.outputs\.(\w+)", text))
    assert used <= set(outputs("1.2.3"))


def test_the_notes_say_where_the_source_is() -> None:
    notes = NOTES.read_text(encoding="utf-8")
    assert set(re.findall(r"\{\{\w*\}\}", notes)) == {"{{version}}"}
    assert "source.tar.gz" not in notes
    assert "https://github.com/Ember-Moth/Solaris/tree/v{{version}}" in notes
    assert "https://github.com/Ember-Moth/Solaris/blob/v{{version}}/src/core/go.mod" in notes
    assert f"https://{MIHOMO}" in notes
    assert "`go.sum`" in notes


def test_the_notices_say_where_the_source_is() -> None:
    notices = licenses.NOTICES.read_text(encoding="utf-8")
    header = notices[: notices.index("\nPython\n------\n")]
    assert "source.tar.gz" not in header
    assert "https://github.com/Ember-Moth/Solaris/tree/v<version>" in header
    assert f"https://{MIHOMO}" in header
    assert "src/core/go.sum" in header


def test_go_mod_pins_mihomo_to_the_fork_the_notes_name() -> None:
    # The notes and NOTICES send readers to this repository for mihomo's source.
    text = GO_MOD.read_text(encoding="utf-8")
    (replacement,) = re.findall(r"^replace github\.com/metacubex/mihomo => (\S+) \S+$", text, re.M)
    assert replacement == MIHOMO
