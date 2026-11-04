"""Mach-O and bundle operations shared by the universal macOS builders.

Every executable is merged from matching arm64 and x86_64 inputs. Shared resources
must match byte for byte; a missing file or differing resource fails the build.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

ARCHITECTURES = ("arm64", "x86_64")
GO_ARCHITECTURES = {"arm64": "arm64", "x86_64": "amd64"}
MACHO_MAGIC = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf")


class BuildError(Exception):
    pass


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) in MACHO_MAGIC


def machos(directory: Path) -> Iterator[Path]:
    yield from (path for path in sorted(directory.rglob("*")) if is_macho(path))


def architectures(path: Path) -> set[str]:
    result = subprocess.run(
        ["lipo", "-archs", str(path)], check=True, capture_output=True, text=True
    )
    return set(result.stdout.split())


def require_architectures(path: Path, expected: tuple[str, ...] = ARCHITECTURES) -> None:
    actual = architectures(path)
    if actual != set(expected):
        raise BuildError(f"{path}: architectures {sorted(actual)}, expected {list(expected)}")


def merge_binary(inputs: Mapping[str, Path], output: Path) -> None:
    if set(inputs) != set(ARCHITECTURES):
        raise BuildError("a universal binary needs both arm64 and x86_64 inputs")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="solaris-lipo-") as temporary:
        slices: list[Path] = []
        for arch in ARCHITECTURES:
            source = inputs[arch]
            actual = architectures(source)
            if arch not in actual:
                raise BuildError(f"{source} has no {arch} slice")
            if actual == {arch}:
                slices.append(source)
            else:
                thin = Path(temporary) / arch
                subprocess.run(
                    ["lipo", str(source), "-thin", arch, "-output", str(thin)], check=True
                )
                slices.append(thin)
        subprocess.run(["lipo", "-create", *map(str, slices), "-output", str(output)], check=True)
    output.chmod(inputs[ARCHITECTURES[0]].stat().st_mode & 0o777)
    require_architectures(output)


def _entries(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if "_CodeSignature" not in path.relative_to(root).parts
    }


def _copy(source: Path, target: Path) -> None:
    if sys.platform == "darwin":
        # Nuitka signs data files in extended attributes. shutil.copy2 on macOS
        # drops those attributes; ditto preserves them as well as bundle symlinks.
        subprocess.run(["ditto", str(source), str(target)], check=True)
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def merge_bundle(
    inputs: Mapping[str, Path],
    output: Path,
    *,
    architecture_resources: Mapping[str, tuple[Path, ...]] | None = None,
) -> None:
    """Merge build trees; optional disjoint paths hold dSYM relocation maps, never app data."""
    if set(inputs) != set(ARCHITECTURES):
        raise BuildError("a universal bundle needs both arm64 and x86_64 inputs")
    if output.exists():
        raise BuildError(f"merge destination already exists: {output}")
    separate = architecture_resources or {}
    prefixes = [prefix for values in separate.values() for prefix in values]
    if set(separate) - set(ARCHITECTURES) or any(
        prefix.is_absolute() or ".." in prefix.parts or not prefix.parts for prefix in prefixes
    ):
        raise BuildError("invalid architecture resource paths")
    if any(
        a == b or a in b.parents or b in a.parents
        for i, a in enumerate(prefixes)
        for b in prefixes[i + 1 :]
    ):
        raise BuildError("architecture resource paths overlap")
    trees = {
        arch: {
            relative: path
            for relative, path in _entries(root).items()
            if not any(
                relative == prefix or prefix in relative.parents
                for prefix in separate.get(arch, ())
            )
        }
        for arch, root in inputs.items()
    }
    first, second = (trees[arch] for arch in ARCHITECTURES)
    if first.keys() != second.keys():
        raise BuildError(
            f"architecture bundle contents differ: {sorted(first.keys() ^ second.keys())}"
        )
    # Validate resources before copying anything. Architecture-specific debug maps
    # are the only explicit exception; runtime resources must always agree.
    for relative, source in sorted(first.items()):
        peer = second[relative]
        if source.is_symlink():
            link = source.readlink()
            if not peer.is_symlink() or peer.readlink() != link:
                raise BuildError(f"architecture symlinks differ: {relative}")
            if link.is_absolute() or not source.resolve().is_relative_to(inputs[ARCHITECTURES[0]]):
                raise BuildError(f"symlink escapes the bundle: {relative}")
        elif peer.is_symlink() or source.is_dir() != peer.is_dir():
            raise BuildError(f"architecture file types differ: {relative}")
        elif source.is_dir():
            continue
        elif is_macho(source) or is_macho(peer):
            if not is_macho(source) or not is_macho(peer):
                raise BuildError(f"architecture executable types differ: {relative}")
        elif not filecmp.cmp(source, peer, shallow=False):
            raise BuildError(f"shared resource differs by architecture: {relative}")
        elif os.stat(source).st_mode & 0o111 != os.stat(peer).st_mode & 0o111:
            raise BuildError(f"architecture executable modes differ: {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    _copy(inputs[ARCHITECTURES[0]], output)
    for signature in sorted(output.rglob("_CodeSignature"), reverse=True):
        if signature.is_symlink():
            signature.unlink()
        else:
            shutil.rmtree(signature)
    for arch in ARCHITECTURES[1:]:
        for relative in separate.get(arch, ()):
            source = inputs[arch] / relative
            if source.exists():
                _copy(source, output / relative)
    for relative, source in first.items():
        if is_macho(source):
            merge_binary({arch: tree[relative] for arch, tree in trees.items()}, output / relative)
