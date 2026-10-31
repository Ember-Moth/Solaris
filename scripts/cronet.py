"""Native Cronet artifacts selected by the same Go module graph as the bindings."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "core"
MODULE = "github.com/sagernet/cronet-go/lib/"


@dataclass(frozen=True)
class NativeLibrary:
    module: str
    version: str
    root: Path
    binary: Path


def library(system: str, arch: str) -> NativeLibrary:
    if (system, arch) not in (("darwin", "arm64"), ("windows", "amd64")):
        raise ValueError(f"unsupported Solaris native target: {system}/{arch}")
    module = MODULE + f"{system}_{arch}"

    def query(*arguments: str) -> dict[str, str]:
        result = subprocess.run(
            ["go", "-C", str(CORE), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ | {"GOPROXY": "https://goproxy.cn,direct"},
        )
        return json.loads(result.stdout)

    selected = query("list", "-m", "-json", module)
    name = "libcronet.a" if system == "darwin" else "libcronet.dll"
    vendored = CORE / "vendor" / module
    if (vendored / name).is_file():
        return NativeLibrary(module, selected["Version"], vendored, vendored / name)
    # A full version is essential: cronet-go/all's package commit differs from the
    # native-library commit. go mod download checks the pinned go.sum/module checksum.
    downloaded = query("mod", "download", "-json", f"{module}@{selected['Version']}")
    root = Path(downloaded["Dir"])
    binary = root / name
    if not binary.is_file():
        raise FileNotFoundError(binary)
    return NativeLibrary(module, selected["Version"], root, binary)


# These are the pinned darwin_arm64 package's #cgo LDFLAGS, also required by the
# final Swift link: a Go c-archive does not absorb its external native libraries.
APPLE_LIBRARIES = ("bsm", "pmenergy", "pmsample", "resolv")
APPLE_FRAMEWORKS = (
    "CoreFoundation",
    "CoreGraphics",
    "CoreText",
    "Foundation",
    "Security",
    "ApplicationServices",
    "AppKit",
    "IOKit",
    "OpenDirectory",
    "CFNetwork",
    "CoreServices",
    "Network",
    "SystemConfiguration",
    "UniformTypeIdentifiers",
    "CryptoTokenKit",
    "LocalAuthentication",
)
