"""The draft release's assets (docs/release.md §1): every name an installed version looks
for must stay, with its own line in SHA256SUMS."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from solaris.update.releases import CHECKSUMS_NAME, installer_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
# What 1.0.0's updater asks a release for on a Mac, from before the universal build. Every
# installed 1.0.0 updates through this name, so no release may drop it.
LEGACY_DMG = "Solaris-{version}-macos-arm64.dmg"

needs_shell = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sha256sum") is None,
    reason="the release job runs its steps in bash on Linux",
)


def steps() -> dict[str, dict[str, Any]]:
    job = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]
    return {step["name"]: step for step in job["release"]["steps"] if "name" in step}


def run_step(name: str, cwd: Path, env: dict[str, str]) -> None:
    step = steps()[name]
    # The interpreter running the tests, for the steps' python3: it has tomllib.
    path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        cwd=cwd,
        env={**os.environ, "PATH": path, **env},
        check=True,
    )


def outputs(tmp_path: Path) -> dict[str, str]:
    """The Version step's outputs for a tag of the project's version."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    output = tmp_path / "output"
    output.touch()
    env = {"GITHUB_REF_NAME": f"v{version}", "GITHUB_OUTPUT": str(output)}
    run_step("Version", ROOT, env)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def step_env(name: str, values: dict[str, str]) -> dict[str, str]:
    """A step's env with each ``steps.version.outputs`` expression resolved."""
    env = {}
    for key, expression in steps()[name].get("env", {}).items():
        prefix, suffix = "${{ steps.version.outputs.", " }}"
        if expression.startswith(prefix) and expression.endswith(suffix):
            env[key] = values[expression.removeprefix(prefix).removesuffix(suffix)]
    return env


@needs_shell
def test_the_release_carries_the_disk_image_under_the_name_1_0_0_looks_for(
    tmp_path: Path,
) -> None:
    values = outputs(tmp_path)
    version = values["version"]
    assert values["dmg"] == installer_name(version, "darwin", "arm64")
    assert values["dmg_legacy"] == LEGACY_DMG.format(version=version)


@needs_shell
def test_the_legacy_name_is_the_same_image_with_its_own_checksum(tmp_path: Path) -> None:
    values = outputs(tmp_path)
    artifacts = tmp_path / "build" / "artifacts"
    artifacts.mkdir(parents=True)
    for key in ("dmg", "installer", "installer_arm64"):
        name = values[key]
        (artifacts / name).write_bytes(f"{key} contents".encode())
        digest = hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
        (artifacts / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    run_step("Installers", tmp_path, step_env("Installers", values))
    release = tmp_path / "build" / "release"
    (release / f"{values['source']}.tar.gz").write_bytes(b"source")
    run_step("SHA256SUMS", tmp_path, step_env("SHA256SUMS", values))

    sums = {}
    for line in (release / CHECKSUMS_NAME).read_text().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    image = (release / values["dmg"]).read_bytes()
    assert (release / values["dmg_legacy"]).read_bytes() == image
    assert sums[values["dmg"]] == sums[values["dmg_legacy"]] == hashlib.sha256(image).hexdigest()
    assert set(sums) == {path.name for path in release.iterdir()} - {CHECKSUMS_NAME}


def test_the_draft_uploads_the_legacy_name() -> None:
    draft = steps()["Create the draft"]
    assert draft["env"]["DMG_LEGACY"] == "${{ steps.version.outputs.dmg_legacy }}"
    assert '"build/release/$DMG_LEGACY"' in draft["run"]
