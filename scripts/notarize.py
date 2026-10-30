"""Notarize a signed app archive or disk image and wait for Apple's verdict; only the release
workflow (.github/workflows/release.yml) runs it, never a developer's machine.

    python scripts/notarize.py build/release/Solaris-1.0.0-macos-arm64.dmg

The App Store Connect API key is $SIGNING/AuthKey.p8, its id APP_STORE_CONNECT_KEY_ID and its
issuer APP_STORE_CONNECT_ISSUER_ID. When Apple does not accept the file, Apple's log is printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def notarytool(*arguments: str) -> str:
    key = Path(os.environ["SIGNING"]) / "AuthKey.p8"
    credentials = [
        "--key",
        str(key),
        "--key-id",
        os.environ["APP_STORE_CONNECT_KEY_ID"],
        "--issuer",
        os.environ["APP_STORE_CONNECT_ISSUER_ID"],
    ]
    command = ["xcrun", "notarytool", *arguments, *credentials, "--output-format", "json"]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        sys.exit("notarize: notarization runs in the release workflow only")
    (path,) = sys.argv[1:]
    print(f"Submitting {Path(path).name} for notarization", flush=True)
    result = json.loads(notarytool("submit", path, "--wait", "--timeout", "1h"))
    print(f"Notarization {result.get('id')}: {result.get('status')}", flush=True)
    if result.get("status") == "Accepted":
        return 0
    if result.get("id"):
        print(json.dumps(json.loads(notarytool("log", result["id"])), indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
