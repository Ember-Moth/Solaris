"""Inspect localized bundle lookup without activating a system extension."""

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_apple

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("xcrun") is None, reason="needs macOS bundle APIs"
)


@pytest.fixture
def extension(tmp_path: Path) -> Path:
    bundle = tmp_path / "Solaris.systemextension"
    contents = bundle / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    info = plistlib.loads((build_apple.APPLE / "Tunnel" / "Info.plist").read_bytes())
    info.update(CFBundleDevelopmentRegion="en", CFBundleIdentifier=build_apple.TUNNEL_ID)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    for language in build_apple.LOCALIZATIONS:
        relative = f"{language}.lproj"
        shutil.copytree(build_apple.APPLE / "Tunnel" / relative, resources / relative)
    return bundle


def test_bundle_resolves_chinese_english_and_fallback(extension: Path, tmp_path: Path) -> None:
    source = tmp_path / "main.swift"
    source.write_text("""
import Foundation
let bundle = Bundle(path: CommandLine.arguments[1])!
let value = bundle.object(forInfoDictionaryKey: "NSSystemExtensionUsageDescription") as! String
print(String(data: try! JSONSerialization.data(withJSONObject: [value]), encoding: .utf8)!)
""")
    # Foundation restricts dependent bundle languages to the main app's localizations.
    # Match the app assembled by build_app, rather than an unlocalized command-line tool.
    app = tmp_path / "Lookup.app" / "Contents"
    executable = app / "MacOS" / "lookup"
    executable.parent.mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "lookup",
                "CFBundleIdentifier": "dev.tschen.solaris.localization-test",
                "CFBundleDevelopmentRegion": "en",
                "CFBundleLocalizations": list(build_apple.LOCALIZATIONS),
            }
        )
    )
    subprocess.run(["xcrun", "swiftc", str(source), "-o", str(executable)], check=True)
    build_apple.check_localizations(extension)
    english = plistlib.loads((extension / "Contents" / "Info.plist").read_bytes())[
        "NSSystemExtensionUsageDescription"
    ]
    for language in ("en", "zh-Hans", "zh-CN", "fr"):
        result = subprocess.run(
            [str(executable), str(extension), "-AppleLanguages", f"({language})"],
            check=True,
            capture_output=True,
            text=True,
        )
        [message] = json.loads(result.stdout)
        if language.startswith("zh"):
            assert "按照你的连接设置处理网络流量" in message
        else:
            assert message == english


def test_packaging_rejects_missing_or_stale_localization(extension: Path) -> None:
    path = extension / "Contents/Resources/zh-Hans.lproj/InfoPlist.strings"
    path.write_text('"NSSystemExtensionUsageDescription" = "old";')
    with pytest.raises(build_apple.BuildError, match="differs"):
        build_apple.check_localizations(extension)
    path.unlink()
    with pytest.raises(build_apple.BuildError, match="missing"):
        build_apple.check_localizations(extension)
