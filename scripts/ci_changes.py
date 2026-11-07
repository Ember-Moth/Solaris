"""Select CI jobs from a complete Git diff. Runs with the runner's Python 3.10+.

Unknown inputs and unavailable history select every job. Pushes reuse a successful
ancestor CI run, not an unchecked previous commit. File paths always come from Git.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
JOBS = frozenset(
    {
        "python",
        "e2e",
        "go",
        "apple",
        "installer-ui",
        "windows-installer",
        "windows-arm64-upgrade",
        "tun",
    }
)
CORE = frozenset({"go", "e2e", "apple", "windows-installer", "tun"})
UI = frozenset({"python", "e2e", "apple", "windows-installer"})
WINDOWS = frozenset({"python", "installer-ui", "windows-installer"})
BUILD = frozenset({"python", "apple", "windows-installer", "installer-ui"})
GLOBAL_FILES = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        ".gitattributes",
        ".gitignore",
        "LICENSE",
        "src/core/go.mod",
        "src/core/go.sum",
        "scripts/ci_changes.py",
        "tests/test_ci_changes.py",
    }
)
GENERATORS = frozenset(
    {
        "scripts/gen_theme.py",
        "scripts/gen_i18n.py",
        "scripts/gen_screenshots.py",
        "scripts/qmllint.py",
        "scripts/qmllint_exemptions.toml",
    }
)
SHARED_BUILD = frozenset(
    {
        "scripts/build_app.py",
        "scripts/build_apple.py",
        "scripts/macos.py",
        "scripts/cronet.py",
        "scripts/symbols.py",
        "scripts/gen_icons.py",
        "scripts/gen_licenses.py",
    }
)
ENGINE = "src/core/internal/engine/"
ENGINE_TUN = f"{ENGINE}tun"


@dataclass(frozen=True)
class Plan:
    jobs: frozenset[str]
    protocol: bool = False
    reason: str = "changed inputs"
    base: str | None = None


def complete(reason: str) -> Plan:
    return Plan(JOBS, protocol=True, reason=reason)


def select(paths: Iterable[str]) -> Plan:
    jobs: set[str] = set()
    protocol = False
    for path in paths:
        if path in GLOBAL_FILES or path.startswith(".github/"):
            return complete("shared build or CI configuration")
        if path == "DESIGN.md":
            jobs.update(UI | {"installer-ui"})
        elif (
            path in {"README.md", "PRIVACY.md", "apple/README.md"}
            or (path.startswith("docs/") and path.endswith(".md"))
            or path.startswith("docs/images/")  # the screenshots
        ):
            continue
        elif path.startswith("docs/examples/"):
            jobs.add("go")
        elif (
            path.startswith(("proto/", "src/core/internal/controlpb/", "src/solaris/protocol/"))
            or path == "scripts/gen_protocol.py"
        ):
            jobs.update(CORE | {"python"})
            protocol = True
        elif path.startswith("src/core/"):
            if path.endswith("_test.go"):
                jobs.add("go")
                # Only the tun job builds the engine's tests with -tags tuntest, and every
                # test file of the package goes into that binary: TestMain and its helpers too.
                if path.startswith(ENGINE) and "/" not in path.removeprefix(ENGINE):
                    jobs.add("tun")
            elif path.endswith("_windows.go"):
                jobs.update({"go", "windows-installer"})
            elif path.endswith("_darwin.go"):
                jobs.update({"go", "apple"})
                # e2e drives the development service on macOS, which runs every darwin file
                # but the TUN's (it has none): the peer check of its control connection too.
                if not path.startswith(ENGINE_TUN):
                    jobs.add("e2e")
            else:
                jobs.update(CORE)
        elif path.startswith("apple/"):
            jobs.update({"python", "apple"})
        elif path.startswith("src/solaris/ui/fonts/"):
            jobs.update(UI | {"installer-ui"})
        elif path.startswith("src/solaris/"):
            jobs.update(UI)
        elif path == "tests/test_build_windows.py":
            jobs.update(WINDOWS)
        elif path.startswith(("tests/e2e/", "tests/support/")) or path == "tests/conftest.py":
            jobs.update({"python", "e2e"})
        elif path.startswith("tests/"):
            jobs.add("python")
        elif path.startswith("testdata/"):
            jobs.update({"python", "go", "e2e"})
        elif path in GENERATORS:
            jobs.add("python")
        elif path in {"scripts/dev.py", "scripts/preview.py"}:
            jobs.update({"python", "e2e"})
        elif path in SHARED_BUILD:
            jobs.update(BUILD)
        elif path == "scripts/fetch_geodata.py":
            # Only e2e checks the category list against the pinned data (--check) and runs
            # "my plan" on that data.
            jobs.update(BUILD | {"e2e"})
        elif path in {
            "scripts/build_windows.py",
            "scripts/build_installer_ui.py",
        } or path.startswith("package/windows/"):
            jobs.update(WINDOWS)
        elif path in {"scripts/build_dmg.py", "scripts/gen_dmg_background.py"} or path.startswith(
            "package/dmg/"
        ):
            jobs.update({"python", "apple"})
        elif path.startswith("package/"):
            jobs.update(BUILD)
        else:
            return complete("unclassified path")
    if "windows-installer" in jobs:
        jobs.add("windows-arm64-upgrade")
    return Plan(
        frozenset(jobs), protocol, "changed inputs" if jobs else "documentation or empty diff"
    )


def valid_sha(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value))
        and set(value) != {"0"}
    )


def successful_ancestor(
    runs: Iterable[dict[str, Any]], repository: str, branch: str, head: str, root: Path
) -> str | None:
    if not valid_sha(head):
        return None
    for run in runs:
        sha = run.get("head_sha")
        if (
            not valid_sha(sha)
            or run.get("conclusion") != "success"
            or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("head_branch") != branch
            or run.get("path") != ".github/workflows/ci.yml"
            or (run.get("head_repository") or {}).get("full_name", "").casefold()
            != repository.casefold()
        ):
            continue
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, head],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return sha
    return None


def previous_success(head: str, root: Path = ROOT) -> str | None:
    repository, branch, token = (
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_REF_NAME"],
        os.environ["GH_TOKEN"],
    )
    if not valid_sha(head) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return None
    query = urlencode({"branch": branch, "status": "success", "per_page": 100})
    connection = http.client.HTTPSConnection("api.github.com", timeout=15)
    try:
        # HTTPSConnection does not follow redirects with the authentication header.
        connection.request(
            "GET",
            f"/repos/{repository}/actions/workflows/ci.yml/runs?{query}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Solaris-CI",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("successful CI history unavailable")
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("CI history response too large")
        runs = json.loads(data)["workflow_runs"]
    finally:
        connection.close()
    # If no successful ancestor is in the returned page, choose a full run.
    return successful_ancestor(runs, repository, branch, head, root)


def changed_paths(
    event_name: str, event: dict[str, Any], root: Path, push_base: str | None = None
) -> list[str]:
    if event_name == "push":
        base, head = event["before"], event["after"]
        if not valid_sha(base) or push_base is None:
            raise ValueError("no trusted successful CI baseline")
        base = push_base
        separator = ".."
    elif event_name == "pull_request":
        base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        separator = "..."
    else:
        raise ValueError("event has no supported diff")
    if not all(valid_sha(sha) for sha in (base, head)):
        raise ValueError("missing or invalid commit range")
    # Disabling rename detection keeps both the removed and added path. A rename
    # out of a code directory must still select that directory's checks.
    result = subprocess.run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--name-only",
            "-z",
            f"{base}{separator}{head}",
            "--",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def for_event(
    event_name: str, event: dict[str, Any], root: Path = ROOT, push_base: str | None = None
) -> Plan:
    if event_name == "workflow_dispatch":
        return complete("manual full run")
    # pyproject.toml uses README.md as build metadata. Editing prose is harmless;
    # deleting it or replacing it with a link must still exercise packaging.
    readme = root / "README.md"
    if readme.is_symlink() or not readme.is_file():
        return complete("build readme unavailable")
    try:
        plan = select(changed_paths(event_name, event, root, push_base))
        return Plan(plan.jobs, plan.protocol, plan.reason, push_base)
    except Exception:
        # A force-pushed/shallow/missing base or a new event must cost a full run,
        # never hide a required check behind an empty change list.
        return complete("diff unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", nargs="*", help="preview a plan for repository-relative paths")
    args = parser.parse_args()
    if args.paths is not None:
        plan = select(args.paths)
    else:
        try:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            name = os.environ["GITHUB_EVENT_NAME"]
            if int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1:
                plan = complete("manual rerun")
            else:
                base = previous_success(event["after"]) if name == "push" else None
                plan = for_event(name, event, push_base=base)
        except Exception:
            plan = complete("event unavailable")
    jobs = json.dumps(sorted(plan.jobs), separators=(",", ":"))
    print(
        json.dumps(
            {
                "jobs": sorted(plan.jobs),
                "protocol": plan.protocol,
                "reason": plan.reason,
                "base": plan.base,
            }
        )
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"jobs={jobs}\nprotocol={str(plan.protocol).lower()}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(
                f"Selection: {plan.reason}\n\nJobs: {', '.join(sorted(plan.jobs)) or 'none'}\n"
            )
            if plan.base:
                stream.write(f"\nSuccessful baseline: `{plan.base}`\n")


if __name__ == "__main__":
    main()
