"""A Nuitka user plugin for the native Windows ARM64 build (scripts/build_windows.py passes it
with --user-plugin): Nuitka's DLL dependency scan, with the same result in a fraction of the time.

On ARM64, Nuitka finds the DLLs each binary needs with pefile, in Python: depends.exe does not
read ARM64 binaries and WinDepends runs only on x64. detectDLLsWithPEFile() walks the imports of
every extension module and DLL it includes, recursively, and remembers nothing from one walk to
the next; getPEFileUsedDllNames() loads each file it meets in full, which in pefile means counting
every byte and parsing the exports, resources, relocations and every other directory, and closing
it runs gc.collect() over Nuitka's whole heap. Qt6Core, Qt6Gui and the other large DLLs are under
nearly every module, so they are loaded once for each, and the ARM64 build spends most of an hour
where the x64 one, with depends.exe, spends minutes.

used_dll_names() replaces getPEFileUsedDllNames() and returns the same names for every file. It
parses only the import and delay import directories, but both as the full load does and in its
order before the original's own two calls: pefile stops reading thunks after 0x2000 per file,
lookup and address tables both counted and the full load's first, so in a binary importing more
than about 4096 symbols the original's second pass finds fewer delay imports, or none, and keeps
or replaces the full load's accordingly. It remembers each file's names by path, size and
modification time, and unmaps the file without the gc.collect(). tests/test_nuitka_pefile.py
compares it with the original.

It replaces a Nuitka internal, so it runs only under the Nuitka it was written against and stops
the build otherwise. Upgrading Nuitka (docs/maintenance.md §5.4) means reading the new
getPEFileUsedDllNames(), how detectDLLsWithPEFile() calls it, and Nuitka's copy of pefile again,
updating this file and its tests to match, then NUITKA_VERSION.
"""

# Nuitka is not a project dependency: the build adds it with uv run --with nuitka==.
# pyright: reportMissingImports=false

from __future__ import annotations

import os

from nuitka.plugins.PluginBase import NuitkaPluginBase

# scripts/build_app.py NUITKA_VERSION, and --with nuitka== in the workflows.
NUITKA_VERSION = "4.2.2"

_IMPORT = "IMAGE_DIRECTORY_ENTRY_IMPORT"
_DELAY_IMPORT = "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"
_ENTRIES = ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")

# (path, size, modification time) -> the names in order, or None for a file that is no PE file.
_names: dict[tuple[str, int, int], tuple[str, ...] | None] = {}


class PatchError(Exception):
    """This Nuitka is not the one used_dll_names() reproduces."""


def used_dll_names(filename: str):
    """Nuitka 4.2.2's getPEFileUsedDllNames(): the DLLs ``filename`` imports, delay-loaded
    ones after the others, as an OrderedSet; None when it is no PE file."""
    from nuitka.containers.OrderedSets import OrderedSet

    status = os.stat(filename)
    key = (os.path.normcase(os.path.abspath(filename)), status.st_size, status.st_mtime_ns)
    if key not in _names:
        _names[key] = _read(filename)
    names = _names[key]
    # A new set each time, as the original returns: the caller may change it.
    return None if names is None else OrderedSet(names)


def _read(filename: str) -> tuple[str, ...] | None:
    from nuitka.containers.OrderedSets import OrderedSet
    from nuitka.utils.Importing import importFromInlineCopy

    pefile = importFromInlineCopy("pefile", must_exist=True)
    directory = pefile.DIRECTORY_ENTRY
    try:
        pe = pefile.PE(filename, fast_load=True)
    except pefile.PEFormatError:
        return None
    try:
        # What the original's full load parses that bears on these two directories: both in
        # full, in this order. Its other directories leave the import state alone.
        pe.parse_data_directories(directories=(directory[_IMPORT], directory[_DELAY_IMPORT]))
        # Then the original's own two calls.
        pe.parse_data_directories(directories=(directory[_IMPORT],), import_dllnames_only=True)
        pe.parse_data_directories(directories=(directory[_DELAY_IMPORT],))
    except pefile.PEFormatError:
        return None
    finally:
        # PE.close() unmaps the file, then runs gc.collect(); only the unmapping is needed.
        pe.__data__.close()
    names = OrderedSet()
    for entry in _ENTRIES:
        for imported in getattr(pe, entry, ()):
            names.add(imported.dll.decode("utf8"))
    return tuple(names)


def install() -> None:
    """Have Nuitka's pefile scan call used_dll_names(); PatchError when this Nuitka is not the
    one it reproduces."""
    from nuitka.freezer import DllDependenciesWin32PEFile
    from nuitka.utils import SharedLibraries
    from nuitka.Version import getNuitkaVersion

    found = getNuitkaVersion()
    if found != NUITKA_VERSION:
        raise PatchError(
            f"written for Nuitka {NUITKA_VERSION}, not {found}: check {os.path.basename(__file__)} "
            "against this Nuitka before raising its NUITKA_VERSION (docs/maintenance.md §5.4)"
        )
    original = getattr(SharedLibraries, "getPEFileUsedDllNames", None)
    if original is None:
        raise PatchError("nuitka.utils.SharedLibraries has no getPEFileUsedDllNames")
    if getattr(DllDependenciesWin32PEFile, "getPEFileUsedDllNames", None) is not original:
        raise PatchError("detectDLLsWithPEFile does not call SharedLibraries.getPEFileUsedDllNames")
    SharedLibraries.getPEFileUsedDllNames = used_dll_names
    DllDependenciesWin32PEFile.getPEFileUsedDllNames = used_dll_names


class PEFileDependencies(NuitkaPluginBase):
    plugin_name = "solaris-pefile-dependencies"
    plugin_desc = "Scan DLL dependencies with pefile, each file once and only its imports."

    def __init__(self) -> None:
        # Activated after Nuitka restarts itself and parses its options, before any scan.
        try:
            install()
        except PatchError as error:
            self.sysexit(str(error))

    def getCacheContributionValues(self, module_name: str) -> tuple[str, ...]:  # noqa: N802
        # The same results as without the plugin: Nuitka's caches hold for both.
        return ()
