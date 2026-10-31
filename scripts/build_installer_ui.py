"""Build the small Win32 installer UI; NSIS remains the installation engine."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "package" / "windows" / "ui"
DEFAULT_OUTPUT = ROOT / "build" / "installer-ui"


def theme_header() -> str:
    design = yaml.safe_load((ROOT / "DESIGN.md").read_text(encoding="utf-8").split("---", 2)[1])
    names = (
        "background",
        "surface",
        "surface-subtle",
        "border",
        "border-button",
        "border-control",
        "text",
        "text-secondary",
        "text-muted",
        "primary",
        "primary-hover",
        "primary-pressed",
        "on-primary",
        "danger",
        "control-track",
    )
    fields = "\n".join(f"  COLORREF {name.replace('-', '_')};" for name in names)
    lines = [
        "// Generated from DESIGN.md by build_installer_ui.py.",
        "#pragma once",
        "struct Palette {",
        fields,
        "};",
    ]
    for mode in ("light", "dark"):
        colors = design["colors"] if mode == "light" else design["colors"]["dark"]
        values = []
        for name in names:
            color = colors[name].removeprefix("#")
            if len(color) != 6:
                raise ValueError(f"installer color {name} must be opaque")
            values.append(
                "RGB(" + ", ".join(str(int(color[i : i + 2], 16)) for i in (0, 2, 4)) + ")"
            )
        lines.append(f"inline constexpr Palette k{mode.title()} = {{" + ", ".join(values) + "};")
    for key, token in (
        ("Width", "installer-width"),
        ("Height", "installer-height"),
        ("Margin", "6"),
        ("ButtonHeight", "control-md"),
    ):
        value = str(
            design["spacing"].get(
                token, design["spacing"].get(int(token) if token.isdigit() else token)
            )
        )
        lines.append(f"inline constexpr int k{key} = {int(value.removesuffix('px'))};")
    for name, role in (
        ("Title", "title"),
        ("Body", "body-md"),
        ("Caption", "caption"),
        ("Label", "label-md"),
        ("Compact", "label-sm"),
        ("Mono", "data-sm"),
    ):
        token = design["typography"][role]
        lines.append(
            f"inline constexpr int k{name}Size = {int(token['fontSize'].removesuffix('px'))};"
        )
        lines.append(f"inline constexpr int k{name}Weight = {int(token['fontWeight'])};")
    return "\n".join(lines) + "\n"


def build(output: Path) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("the installer UI is compiled with MSVC on Windows")
    output.mkdir(parents=True, exist_ok=True)
    (output / "theme.h").write_text(theme_header(), encoding="utf-8")
    vswhere = (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    if not vswhere.is_file():
        raise RuntimeError("Visual Studio C++ build tools are required for the installer UI")
    installation = subprocess.run(
        [
            str(vswhere),
            "-utf8",
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    vcvars = Path(installation) / "VC/Auxiliary/Build/vcvarsall.bat"
    if not installation or not vcvars.is_file():
        raise RuntimeError("MSVC x86 toolchain is missing")
    # Capture only compiler paths, not the full environment (which may contain signing
    # credentials in release jobs). Invoke cl directly so Unicode/project paths don't
    # depend on a batch file's legacy code page or command-string escaping.
    # cmd.exe uses its own quoting rules. An argv list would escape these inner
    # quotes with backslashes for the C runtime, which cmd treats as literal text.
    command_shell = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    configured = subprocess.run(
        f'"{command_shell}" /d /u /s /c '
        f'"call "{vcvars}" x86 >nul && set PATH && set INCLUDE && set LIB"',
        check=True,
        capture_output=True,
    ).stdout.decode("utf-16-le")
    environment = {key.upper(): value for key, value in os.environ.items()}
    for line in configured.splitlines():
        key, _, value = line.partition("=")
        if key.upper() in ("PATH", "INCLUDE", "LIB", "LIBPATH"):
            environment[key.upper()] = value
    compiler = shutil.which("cl.exe", path=environment.get("PATH"))
    if not compiler:
        raise RuntimeError("MSVC x86 compiler could not be located")
    dll = output / "SolarisSetupUI.dll"
    # NSIS's Unicode engine is x86 for both x64 and ARM64 application packages.
    command = [
        compiler,
        "/nologo",
        "/std:c++17",
        "/EHsc",
        "/W4",
        "/WX",
        "/O2",
        "/MT",
        "/Zi",
        "/utf-8",
        "/DUNICODE",
        "/D_UNICODE",
        "/DNOMINMAX",
        "/DWIN32_LEAN_AND_MEAN",
        f"/I{output}",
        "/LD",
        str(SOURCE / "setup_ui.cpp"),
        f"/Fo{output / 'setup_ui.obj'}",
        f"/Fd{output / 'SolarisSetupUI.compile.pdb'}",
        f"/Fe{dll}",
        "/link",
        f"/DEF:{SOURCE / 'setup_ui.def'}",
        "/MACHINE:X86",
        "/INCREMENTAL:NO",
        "/DEBUG:FULL",
        f"/PDB:{output / 'SolarisSetupUI.pdb'}",
        "/PDBALTPATH:%_PDB%",
        f"/IMPLIB:{output / 'SolarisSetupUI.lib'}",
        "user32.lib",
        "gdi32.lib",
        "gdiplus.lib",
        "comctl32.lib",
        "dwmapi.lib",
        "uxtheme.lib",
        "advapi32.lib",
        "ole32.lib",
        "shell32.lib",
        "uuid.lib",
    ]
    subprocess.run(command, env=environment, check=True)
    return dll


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--arch",
        choices=("x64", "arm64"),
        default="x64",
        help="application architecture shown by the preview",
    )
    parser.add_argument(
        "--preview", action="store_true", help="also build the read-only UI test harness"
    )
    arguments = parser.parse_args()
    makensis = shutil.which("makensis")
    if not makensis:
        candidate = (
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "NSIS/makensis.exe"
        )
        makensis = str(candidate) if candidate.is_file() else None
    if not makensis:
        parser.error("NSIS 3 is required")
    plugin = build(arguments.output.resolve())
    print(plugin, flush=True)
    if arguments.preview:
        print(build_preview(plugin, Path(makensis), arguments.arch), flush=True)


def build_preview(plugin: Path, makensis: Path, arch: str = "x64") -> Path:
    if arch not in ("x64", "arm64"):
        raise ValueError(f"unsupported preview architecture: {arch}")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    executable = plugin.parent / "Solaris-setup-preview.exe"
    defines = {
        "APP_VERSION": version,
        "APP_ARCH": arch,
        "OUTPUT_FILE": executable,
        "APP_ICON": ROOT / "package/icons/solaris.ico",
        "INSTALLER_UI_DIR": plugin.parent,
        "INSTALLER_UI_INCLUDE": SOURCE / "interface.nsh",
        "INSTALLER_FONTS": ROOT / "src/solaris/ui/fonts",
        "INSTALLER_LICENSE": ROOT / "LICENSE",
    }

    def quoted(value: str | Path) -> str:
        return str(value).replace("$", "$$").replace('"', '$\\"')

    wrapper = plugin.parent / "preview.nsi"
    wrapper.write_text(
        "\n".join(f'!define {key} "{quoted(value)}"' for key, value in defines.items())
        + f'\n!include /CHARSET=UTF8 "{quoted(SOURCE / "preview.nsi")}"\n',
        encoding="utf-8-sig",
    )
    subprocess.run(
        [
            str(makensis),
            "-V2" if sys.platform != "win32" else "/V2",
            "-WX" if sys.platform != "win32" else "/WX",
            str(wrapper),
        ],
        check=True,
    )
    return executable


if __name__ == "__main__":
    main()
