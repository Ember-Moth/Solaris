"""Retain build-matched symbols as CI artifacts, separate from end-user installers."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def apple(binary: Path, dsym: Path, destination: Path) -> None:
    def uuids(path: Path) -> set[str]:
        result = subprocess.run(
            ["xcrun", "dwarfdump", "--uuid", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return set(re.findall(r"UUID: ([A-Fa-f0-9-]+)", result.stdout))

    identifiers = uuids(binary)
    if not identifiers or identifiers != uuids(dsym):
        raise RuntimeError(f"symbols do not match {binary.name}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / dsym.name
    if dsym.resolve() != target.resolve():
        shutil.copytree(dsym, target, dirs_exist_ok=True)
    (destination / f"{binary.name}.json").write_text(
        json.dumps({"binary": binary.name, "uuids": sorted(identifiers)}, indent=2) + "\n"
    )


def windows(ui: Path, service: Path, build: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    pdbs = list(build.rglob("*.pdb"))
    if not pdbs:
        raise RuntimeError("the unstripped Windows UI build produced no PDB symbols")
    for path in pdbs:
        target = destination / "pdb" / path.relative_to(build)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    # Keep the exact executables (Go retains its DWARF information) to match minidumps.
    hashes = {}
    for path in (ui, service):
        shutil.copyfile(path, destination / path.name)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / "binaries.json").write_text(json.dumps(hashes, indent=2) + "\n")
