"""Fetch the GEO data the installers ship (docs/routing.md §8.4): GeoSite.dat and GeoIP.dat
from a pinned MetaCubeX/meta-rules-dat release, checked against pinned SHA-256 digests.

    uv run python scripts/fetch_geodata.py               # into build/geodata, cached
    uv run python scripts/fetch_geodata.py --categories  # regenerate the category list
    uv run python scripts/fetch_geodata.py --check       # the committed list matches the data

The files keep mihomo's names, so M4 and M5 copy them into the core's home as they are. The
category list (src/solaris/host/geo_categories.py) is what the category picker offers
while the core has no GEO data to list. Moving the pin means new digests and a new list.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build" / "geodata"
CATEGORIES = ROOT / "src" / "solaris" / "host" / "geo_categories.py"

REPOSITORY = "MetaCubeX/meta-rules-dat"
# The release branch on 2026-10-29 ("Released on 2026-10-29 09:52").
RELEASE = "be456e5c402c4a9c98519e73f09256c96914cefd"
MIRRORS = (
    "https://raw.githubusercontent.com/{repository}/{release}/{name}",
    "https://cdn.jsdelivr.net/gh/{repository}@{release}/{name}",
)
TIMEOUT_S = 120


@dataclass(frozen=True)
class DataFile:
    name: str  # in the release
    home_name: str  # as mihomo looks for it in its home
    size: int
    sha256: str


GEOSITE = DataFile(
    "geosite.dat",
    "GeoSite.dat",
    4353587,
    "38aac2a107485a3818501106292efbbc92214588aa1d62ac5fb5d8a1d1a335f7",
)
GEOIP = DataFile(
    "geoip.dat",
    "GeoIP.dat",
    16549675,
    "fbf747a085be25eb61ecac8c27140b4ab0bf8181af6fdb104caa325bd93cf117",
)
FILES = (GEOSITE, GEOIP)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _download(data: DataFile, target: Path) -> None:
    errors = []
    for mirror in MIRRORS:
        url = mirror.format(repository=REPOSITORY, release=RELEASE, name=data.name)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            partial = Path(temporary.name)
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
                    shutil.copyfileobj(response, temporary, 1 << 20)
            except OSError as error:
                errors.append(f"{url}: {error}")
                temporary.close()
                partial.unlink()
                continue
        if partial.stat().st_size == data.size and _digest(partial) == data.sha256:
            partial.replace(target)
            return
        partial.unlink()
        errors.append(f"{url}: not the pinned {data.name}")
    raise RuntimeError("; ".join(errors))


def fetch(directory: Path = CACHE) -> dict[str, Path]:
    """The pinned files in ``directory``, downloaded unless they are there already, by
    their names in mihomo's home."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for data in FILES:
        path = directory / data.home_name
        current = path.is_file() and path.stat().st_size == data.size
        if not (current and _digest(path) == data.sha256):
            print(f"Downloading {data.name} of {REPOSITORY}@{RELEASE[:7]}", file=sys.stderr)
            _download(data, path)
        paths[data.home_name] = path
    return paths


def seed(home: Path, directory: Path = CACHE) -> None:
    """Copy the pinned files into a mihomo home. They get a fresh modification time, so the
    core does not count them as due for its update."""
    home.mkdir(parents=True, exist_ok=True)
    for name, path in fetch(directory).items():
        shutil.copyfile(path, home / name)


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7


def _fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(number, wire type, value) of each field; length-delimited values as bytes."""
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, offset = _varint(data, offset)
        elif kind == 2:
            length, offset = _varint(data, offset)
            value, offset = data[offset : offset + length], offset + length
        elif kind in (1, 5):
            width = 8 if kind == 1 else 4
            value, offset = data[offset : offset + width], offset + width
        else:
            raise ValueError(f"unsupported wire type {kind}")
        yield number, kind, value


def categories(data: bytes) -> list[str]:
    """The categories of a GeoSiteList or GeoIPList as the core lists them: field 1 of each
    entry in field 1, lower-cased and sorted."""
    names = set()
    for number, kind, entry in _fields(data):
        if number != 1 or kind != 2:
            continue
        assert isinstance(entry, bytes)
        for field, field_kind, value in _fields(entry):
            if field == 1 and field_kind == 2:
                assert isinstance(value, bytes)
                names.add(value.decode().lower())
                break
    return sorted(names)


def _tuple(name: str, values: list[str]) -> str:
    return f"{name} = (\n" + "".join(f'    "{value}",\n' for value in values) + ")\n"


def render_categories(paths: dict[str, Path]) -> str:
    sites = categories(paths[GEOSITE.home_name].read_bytes())
    ips = categories(paths[GEOIP.home_name].read_bytes())
    for name in (*sites, *ips):
        if not name.isprintable() or '"' in name or "\\" in name:
            raise ValueError(f"unexpected category name {name!r}")
    return (
        '"""The categories in the GEO data the installers ship, for the category picker while the\n'
        "core has none to list (docs/routing.md §8.4). Generated by scripts/fetch_geodata.py\n"
        '--categories from the pinned release; do not edit."""\n\n'
        f'SOURCE = "{REPOSITORY}@{RELEASE}"\n'
        f'GEOSITE_SHA256 = "{GEOSITE.sha256}"\n'
        f'GEOIP_SHA256 = "{GEOIP.sha256}"\n\n'
        + _tuple("GEOSITE", sites)
        + "\n"
        + _tuple("GEOIP", ips)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the GEO data the installers ship")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--categories", action="store_true", help="regenerate the category list")
    action.add_argument("--check", action="store_true", help="the category list matches the data")
    parser.add_argument("--dir", type=Path, default=CACHE, help="where to keep the files")
    args = parser.parse_args()
    paths = fetch(args.dir)
    if args.categories:
        CATEGORIES.write_text(render_categories(paths), encoding="utf-8")
        print(f"Wrote {CATEGORIES.relative_to(ROOT)}")
    elif args.check:
        if CATEGORIES.read_text(encoding="utf-8") != render_categories(paths):
            raise SystemExit(f"Stale category list: {CATEGORIES.relative_to(ROOT)}")
        print("The category list matches the pinned GEO data.")
    else:
        for path in paths.values():
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
