"""package/windows/nuitka_pefile.py against the Nuitka function it replaces: the same DLL names
for every file, on generated ARM64 images and, on Windows, on every PE file of this Python and
PySide6.

Needs Nuitka, which is not a project dependency, and pefile: Nuitka installs its own copy only on
Windows. Without them the tests are skipped, unless SOLARIS_NUITKA_TESTS=1, as on CI's Windows
runners, where the python job runs them. Elsewhere:

    uv run --with nuitka==4.2.2 --with pefile==2024.8.26 pytest tests/test_nuitka_pefile.py
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import struct
import sys
from collections.abc import Callable, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest

from scripts import build_windows


def _unavailable(reason: str) -> None:
    if os.environ.get("SOLARIS_NUITKA_TESTS") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason, allow_module_level=True)


if importlib.util.find_spec("nuitka") is None:
    _unavailable("needs Nuitka: uv run --with nuitka==4.2.2")
SharedLibraries = importlib.import_module("nuitka.utils.SharedLibraries")
scan = importlib.import_module("nuitka.freezer.DllDependenciesWin32PEFile")
nuitka_version = importlib.import_module("nuitka.Version")
importing = importlib.import_module("nuitka.utils.Importing")
inline_copies = importlib.import_module("nuitka.utils.InlineCopies")
if (
    inline_copies.getInlineCopyFolderIfExists("pefile") is None
    and importlib.util.find_spec("pefile") is None
):
    _unavailable("needs pefile, which Nuitka bundles only on Windows: --with pefile==2024.8.26")

from package.windows import nuitka_pefile  # noqa: E402 - imports Nuitka

ORIGINAL = SharedLibraries.getPEFileUsedDllNames
assert ORIGINAL is not nuitka_pefile.used_dll_names
pefile = importing.importFromInlineCopy("pefile", must_exist=True)

Symbols = Sequence[int | bytes]  # an ordinal, or a name
Tables = Sequence[tuple[bytes, Symbols]]  # a DLL and what is imported from it

SECTION_RVA = 0x1000
FILE_ALIGNMENT = 0x200
ORDINAL_FLAG = 1 << 63
IMPORT_DIRECTORY = 1
DELAY_IMPORT_DIRECTORY = 13


def _align(value: int, alignment: int) -> int:
    return -(-value // alignment) * alignment


def _image(imports: Tables, delay_imports: Tables = ()) -> bytes:
    """An ARM64 PE32+ DLL whose one section holds the import and delay import tables."""
    tables = [*imports, *delay_imports]
    descriptors = 20 * (len(imports) + 1) + 32 * (len(delay_imports) + 1) if tables else 0
    thunks = []  # each table's lookup table, then its address table
    offset = descriptors
    for _, symbols in tables:
        thunks.append(offset)
        offset += 2 * 8 * (len(symbols) + 1)
    strings = bytearray()

    def string(data: bytes) -> int:
        rva = SECTION_RVA + offset + len(strings)
        strings.extend(data)
        if len(strings) % 2:
            strings.append(0)
        return rva

    lookup = bytearray()
    import_descriptors = bytearray()
    delay_descriptors = bytearray()
    for index, ((dll, symbols), start) in enumerate(zip(tables, thunks, strict=True)):
        entries = [
            ORDINAL_FLAG | symbol if isinstance(symbol, int) else string(b"\0\0" + symbol + b"\0")
            for symbol in symbols
        ]
        table = struct.pack(f"<{len(entries) + 1}Q", *entries, 0)
        lookup += table + table
        name = string(dll + b"\0")
        lookup_rva = SECTION_RVA + start
        address_rva = lookup_rva + len(table)
        if index < len(imports):
            import_descriptors += struct.pack("<5I", lookup_rva, 0, 0, name, address_rva)
        else:
            delay_descriptors += struct.pack("<8I", 1, name, 0, address_rva, lookup_rva, 0, 0, 0)
    content = b""
    directories = [(0, 0)] * 16
    if tables:
        import_descriptors += bytes(20)
        delay_descriptors += bytes(32)
        content = bytes(import_descriptors + delay_descriptors + lookup + strings)
        directories[IMPORT_DIRECTORY] = (SECTION_RVA, len(import_descriptors))
        if delay_imports:
            delay_rva = SECTION_RVA + len(import_descriptors)
            directories[DELAY_IMPORT_DIRECTORY] = (delay_rva, len(delay_descriptors))
    content = content or bytes(16)
    raw_size = _align(len(content), FILE_ALIGNMENT)
    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B,  # PE32+
        14,
        0,
        0,
        raw_size,
        0,
        0,
        SECTION_RVA,
        0x180000000,
        SECTION_RVA,
        FILE_ALIGNMENT,
        6,
        0,
        0,
        0,
        6,
        0,
        0,
        SECTION_RVA + _align(len(content), SECTION_RVA),
        FILE_ALIGNMENT,
        0,
        3,
        0x160,
        0x100000,
        0x1000,
        0x100000,
        0x1000,
        0,
        16,
    ) + b"".join(struct.pack("<II", *directory) for directory in directories)
    header = (
        b"MZ"
        + bytes(58)
        + struct.pack("<I", 64)
        + b"PE\0\0"
        + struct.pack("<HHIIIHH", 0xAA64, 1, 0, 0, 0, len(optional), 0x2022)
        + optional
        + struct.pack(
            "<8sIIIIIIHHI",
            b".idata",
            len(content),
            SECTION_RVA,
            raw_size,
            FILE_ALIGNMENT,
            0,
            0,
            0,
            0,
            0xC0000040,
        )
    )
    return header.ljust(FILE_ALIGNMENT, b"\0") + content.ljust(raw_size, b"\0")


def _naive(filename: str) -> list[str] | None:
    """The obvious fast version: only the original's own two calls, without the full load."""
    try:
        with pefile.PE(filename, fast_load=True) as pe:
            pe.parse_data_directories(
                directories=(pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],),
                import_dllnames_only=True,
            )
            pe.parse_data_directories(
                directories=(pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],)
            )
            names = (
                entry.dll.decode("utf8")
                for name in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
                for entry in getattr(pe, name, ())
            )
            return list(dict.fromkeys(names))
    except pefile.PEFormatError:
        return None


def _names(function: Callable[[str], Any], path: Path) -> list[str]:
    result = function(str(path))
    assert result is not None, path.name
    return list(result)


def _outcome(function: Callable[[str], Any], path: Path) -> tuple[str, Any]:
    """What ``function`` makes of ``path``: the names in order, None, or the exception's type."""
    try:
        result = function(str(path))
    except Exception as error:
        return ("raises", type(error))
    return ("names", None if result is None else list(result))


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No names remembered from another test, and Nuitka's functions restored afterwards."""
    monkeypatch.setattr(nuitka_pefile, "_names", {})
    monkeypatch.setattr(SharedLibraries, "getPEFileUsedDllNames", ORIGINAL)
    monkeypatch.setattr(scan, "getPEFileUsedDllNames", ORIGINAL)


def _same(path: Path) -> tuple[str, Any]:
    expected = _outcome(ORIGINAL, path)
    assert _outcome(nuitka_pefile.used_dll_names, path) == expected, path.name
    return expected


SAMPLE = _image(
    [
        (b"KERNEL32.dll", [b"GetModuleHandleW", b"Sleep"]),
        (b"python314.dll", [5, b"PyModule_Create2"]),
        (b"VCRUNTIME140.dll", [b"memcpy"]),
    ],
    [(b"late.dll", [b"Late"]), (b"KERNEL32.dll", [b"SleepEx"])],
)


def test_imports_and_delay_imports(tmp_path: Path) -> None:
    path = tmp_path / "sample.dll"
    path.write_bytes(SAMPLE)
    assert _same(path) == (
        "names",
        ["KERNEL32.dll", "python314.dll", "VCRUNTIME140.dll", "late.dll"],
    )


END = len(SAMPLE.rstrip(b"\0"))


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("no-imports.dll", _image([])),
        ("ordinals.dll", _image([(b"a.dll", [1])], [(b"b.dll", [1])])),
        ("non-ascii.dll", _image([(b"caf\xc3\xa9.dll", [1]), (b"\xff.dll", [2])])),
        ("empty.dll", b""),
        ("text.dll", b"not a program"),
        ("dos.exe", b"MZ" + bytes(200)),
        # Cut in the headers, the tables and the names.
        *(
            (f"cut-{size:#x}.dll", SAMPLE[:size])
            for size in (0x40, 0x100, 0x200, 0x230, 0x2C0, END - 60, END - 30, END - 12, END - 4)
        ),
    ],
)
def test_other_files(tmp_path: Path, name: str, data: bytes) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    _same(path)


# pefile reads at most 0x2000 thunks per file, lookup and address tables both counted, and the
# original's full load counts first. Around that limit its second pass over the delay imports
# finds fewer of them, or none.
@pytest.mark.parametrize("count", [100, 4000, 4050, 4060, 4061, 4062, 4063, 4070, 4090, 5000])
def test_import_limit(tmp_path: Path, count: int) -> None:
    path = tmp_path / f"{count}.dll"
    path.write_bytes(
        _image(
            [(b"Qt6Core.dll", list(range(1, count + 1)))],
            [(b"a.dll", list(range(1, 11))), (b"b.dll", list(range(1, 11)))],
        )
    )
    _same(path)


def test_the_limit_hides_delay_imports_from_the_original(tmp_path: Path) -> None:
    # Why used_dll_names() parses both directories in full first: without that pass it would
    # find more than the original does.
    differ = []
    for count in (100, 4062, 5000):
        path = tmp_path / f"{count}.dll"
        path.write_bytes(
            _image(
                [(b"Qt6Core.dll", list(range(1, count + 1)))],
                [(b"a.dll", list(range(1, 11))), (b"b.dll", list(range(1, 11)))],
            )
        )
        if _naive(str(path)) != _names(ORIGINAL, path):
            differ.append(count)
        assert _names(nuitka_pefile.used_dll_names, path) == _names(ORIGINAL, path)
    assert differ == [4062, 5000]
    assert _names(ORIGINAL, tmp_path / "5000.dll") == ["Qt6Core.dll"]


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows Python and Qt binaries")
def test_this_pythons_and_pyside6s_binaries() -> None:
    # Nuitka's own copy of pefile, as in the build, on what the build scans: the interpreter's
    # binaries, and PySide6's and shiboken6's but for the Qt modules it leaves out.
    assert "inline_copy" in Path(pefile.__file__).parts
    base = Path(sys.base_prefix)
    candidates = {*base.glob("*.exe"), *base.glob("*.dll"), *base.glob("DLLs/*")}
    for package in ("PySide6", "shiboken6"):
        spec = importlib.util.find_spec(package)
        assert spec is not None
        assert spec.submodule_search_locations
        for location in spec.submodule_search_locations:
            candidates.update(
                path
                for path in Path(location).rglob("*")
                if not any(fnmatch(str(path), pattern) for pattern in build_windows.EXCLUDED)
            )
    paths = sorted(
        path
        for path in candidates
        if path.suffix.lower() in (".dll", ".pyd", ".exe") and path.is_file()
    )
    assert len(paths) > 100
    found = {path: _outcome(ORIGINAL, path) for path in paths}
    assert any(outcome[0] == "names" and outcome[1] for outcome in found.values())
    differ = [
        str(path) for path in paths if _outcome(nuitka_pefile.used_dll_names, path) != found[path]
    ]
    assert differ == []


def test_each_file_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    read = nuitka_pefile._read
    monkeypatch.setattr(nuitka_pefile, "_read", lambda name: reads.append(name) or read(name))
    path = tmp_path / "sample.dll"
    path.write_bytes(SAMPLE)
    first = nuitka_pefile.used_dll_names(str(path))
    assert first is not None
    first.add("changed.dll")
    assert _names(nuitka_pefile.used_dll_names, path) == _names(ORIGINAL, path)
    assert len(reads) == 1
    # A different file at the same path is read again: by size, and by modification time.
    path.write_bytes(_image([(b"other.dll", [1])]))
    assert _names(nuitka_pefile.used_dll_names, path) == ["other.dll"]
    status = path.stat()
    path.write_bytes(_image([(b"third.dll", [1])]))
    assert path.stat().st_size == status.st_size
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    assert _names(nuitka_pefile.used_dll_names, path) == ["third.dll"]
    assert len(reads) == 3


def test_install_replaces_what_the_scan_calls() -> None:
    nuitka_pefile.install()
    assert scan.getPEFileUsedDllNames is nuitka_pefile.used_dll_names
    assert SharedLibraries.getPEFileUsedDllNames is nuitka_pefile.used_dll_names


def test_the_plugin_installs_on_activation() -> None:
    nuitka_pefile.PEFileDependencies()
    assert scan.getPEFileUsedDllNames is nuitka_pefile.used_dll_names
    assert tuple(nuitka_pefile.PEFileDependencies().getCacheContributionValues("PySide6")) == ()


def test_written_for_this_nuitka() -> None:
    assert nuitka_version.getNuitkaVersion() == nuitka_pefile.NUITKA_VERSION


def test_another_nuitka_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nuitka_version, "getNuitkaVersion", lambda: "4.3.4")
    with pytest.raises(nuitka_pefile.PatchError, match=r"for Nuitka 4\.2\.2, not 4\.3\.4"):
        nuitka_pefile.install()
    assert scan.getPEFileUsedDllNames is ORIGINAL
    with pytest.raises(SystemExit):
        nuitka_pefile.PEFileDependencies()
    assert scan.getPEFileUsedDllNames is ORIGINAL


def test_a_missing_function_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(SharedLibraries, "getPEFileUsedDllNames")
    with pytest.raises(nuitka_pefile.PatchError, match="has no getPEFileUsedDllNames"):
        nuitka_pefile.install()
    assert scan.getPEFileUsedDllNames is ORIGINAL


def test_a_scan_calling_something_else_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan, "getPEFileUsedDllNames", lambda filename: None)
    with pytest.raises(nuitka_pefile.PatchError, match="does not call"):
        nuitka_pefile.install()
    assert SharedLibraries.getPEFileUsedDllNames is ORIGINAL
