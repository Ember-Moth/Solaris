"""Generate Python and Go control-protocol bindings with pinned, checksum-verified tools.

protoc comes from proto/toolchain.json; protoc-gen-go is the version pinned by the
`tool` directive in src/core/go.mod. Use --check in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "proto"
TOOLS = ROOT / "build/protobuf-toolchain"
GO_MODULE_DIR = ROOT / "src/core"
GO_MODULE = "github.com/Ember-Moth/Solaris/src/core"
PYTHON_OUTPUT = ROOT / "src/solaris/protocol"
GO_OUTPUT = GO_MODULE_DIR / "internal/controlpb"
LOCK = json.loads((SCHEMAS / "toolchain.json").read_text(encoding="utf-8"))


def output(args: list[str | Path], cwd: Path | None = None) -> str:
    return subprocess.check_output(
        [str(arg) for arg in args], text=True, encoding="utf-8", cwd=cwd
    ).strip()


def run(args: list[str | Path], cwd: Path | None = None) -> None:
    subprocess.run([str(arg) for arg in args], check=True, cwd=cwd)


def protoc() -> Path:
    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(architecture, architecture)
    target = f"{sys.platform}-{architecture}"
    if target not in LOCK["protoc_archives"]:
        raise RuntimeError(f"No pinned protoc archive for {target}")
    record = LOCK["protoc_archives"][target]
    archive = TOOLS / record["name"]
    TOOLS.mkdir(parents=True, exist_ok=True)
    if (
        not archive.is_file()
        or hashlib.sha256(archive.read_bytes()).hexdigest() != record["sha256"]
    ):
        url = f"https://github.com/protocolbuffers/protobuf/releases/download/v{LOCK['protoc_version']}/{record['name']}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise RuntimeError("Pinned protoc archive checksum mismatch")
        archive.write_bytes(data)
    directory = TOOLS / f"protoc-{LOCK['protoc_version']}-{target}"
    binary = directory / "bin" / ("protoc.exe" if sys.platform == "win32" else "protoc")
    with zipfile.ZipFile(archive) as compressed:
        for entry in compressed.namelist():
            if not (directory / entry).resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("Unsafe path in protoc archive")
        compressed.extractall(directory)
    if sys.platform != "win32":
        binary.chmod(0o755)
    if output([binary, "--version"]) != f"libprotoc {LOCK['protoc_version']}":
        raise RuntimeError("Unexpected protoc version")
    return binary


def go_plugin() -> Path:
    binary = TOOLS / ("protoc-gen-go.exe" if sys.platform == "win32" else "protoc-gen-go")
    run(
        ["go", "build", "-o", binary, "google.golang.org/protobuf/cmd/protoc-gen-go"],
        cwd=GO_MODULE_DIR,
    )
    return binary


def generated_files() -> dict[Path, bytes]:
    compiler = protoc()
    plugin = go_plugin()
    schemas = sorted(SCHEMAS.rglob("*.proto"))
    with tempfile.TemporaryDirectory(prefix="solaris-protobuf-") as temporary:
        directory = Path(temporary)
        python = directory / "python"
        go = directory / "go"
        python.mkdir()
        go.mkdir()
        run([compiler, f"-I{SCHEMAS}", f"--python_out={python}", f"--pyi_out={python}", *schemas])
        run(
            [
                compiler,
                f"-I{SCHEMAS}",
                f"--plugin=protoc-gen-go={plugin}",
                f"--go_out={go}",
                f"--go_opt=module={GO_MODULE}",
                *schemas,
            ]
        )
        files = {
            ROOT / "src" / path.relative_to(python): path.read_bytes()
            for path in python.rglob("*")
            if path.is_file()
        }
        files.update(
            {GO_MODULE_DIR / path.relative_to(go): path.read_bytes() for path in go.rglob("*.go")}
        )
        return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = generated_files()
    existing = set(PYTHON_OUTPUT.glob("*_pb2.py"))
    existing.update(PYTHON_OUTPUT.glob("*_pb2.pyi"))
    existing.update(GO_OUTPUT.glob("*.pb.go"))
    if retired := existing - files.keys():
        names = ", ".join(str(path.relative_to(ROOT)) for path in sorted(retired))
        raise SystemExit(f"Remove retired generated bindings with their schema: {names}")
    for path, content in files.items():
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                raise SystemExit(f"Stale protocol binding: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(
        "Protocol bindings match the pinned schema/toolchain."
        if args.check
        else "Generated protocol bindings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
