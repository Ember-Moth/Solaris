"""CI selection must save work without hiding code changes or failed checks."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import ci_changes as ci

WORKFLOW = ci.ROOT / ".github/workflows/ci.yml"


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["README.md", "PRIVACY.md", "docs/usage.md", "apple/README.md"], set()),
        (
            ["DESIGN.md"],
            {
                "python",
                "e2e",
                "apple",
                "installer-ui",
                "windows-installer",
                "windows-arm64-upgrade",
            },
        ),
        (["docs/examples/mihomo-fork/naive-http2.yaml"], {"go"}),
        (
            ["src/solaris/models/library/core.py"],
            {"python", "e2e", "apple", "windows-installer", "windows-arm64-upgrade"},
        ),
        (
            ["src/solaris/ui/fonts/inter/Inter-Regular.ttf"],
            {
                "python",
                "e2e",
                "apple",
                "installer-ui",
                "windows-installer",
                "windows-arm64-upgrade",
            },
        ),
        (
            ["src/core/internal/engine/engine.go"],
            {"go", "e2e", "apple", "windows-installer", "windows-arm64-upgrade", "tun"},
        ),
        (["src/core/internal/engine/engine_test.go"], {"go", "tun"}),
        (["src/core/internal/engine/main_test.go"], {"go", "tun"}),
        (["src/core/internal/engine/tun_fd_linux_test.go"], {"go", "tun"}),
        (["src/core/internal/peer/peer_windows_test.go"], {"go"}),
        (
            ["src/core/internal/owner/platform_windows.go"],
            {"go", "windows-installer", "windows-arm64-upgrade"},
        ),
        (["src/core/internal/engine/tunfd_darwin.go"], {"go", "apple"}),
        (["src/core/internal/peer/credentials_darwin.go"], {"go", "apple", "e2e"}),
        (["apple/Manager/Deadline.swift"], {"python", "apple"}),
        (
            ["package/windows/installer.nsi"],
            {"python", "installer-ui", "windows-installer", "windows-arm64-upgrade"},
        ),
        (
            ["package/licenses/NOTICE.md"],
            {"python", "apple", "installer-ui", "windows-installer", "windows-arm64-upgrade"},
        ),
        (
            ["scripts/fetch_geodata.py"],
            {
                "python",
                "e2e",
                "apple",
                "installer-ui",
                "windows-installer",
                "windows-arm64-upgrade",
            },
        ),
        (["scripts/gen_i18n.py"], {"python"}),
        (["tests/support/simulated.py"], {"python", "e2e"}),
        (["testdata/control-vectors.json"], {"python", "go", "e2e"}),
    ],
)
def test_selects_consumers_without_unrelated_jobs(paths: list[str], expected: set[str]) -> None:
    assert ci.select(paths).jobs == expected


@pytest.mark.parametrize(
    "path", ["uv.lock", "src/core/go.mod", ".github/workflows/ci.yml", "new-tool/config.toml"]
)
def test_shared_or_unknown_inputs_select_everything(path: str) -> None:
    plan = ci.select(["docs/usage.md", path])
    assert plan.jobs == ci.JOBS
    assert plan.protocol


def test_protocol_changes_include_both_hosts_and_generated_bindings() -> None:
    plan = ci.select(["proto/solaris/protocol/control.proto"])
    assert plan.protocol
    assert {
        "python",
        "go",
        "e2e",
        "apple",
        "windows-installer",
        "windows-arm64-upgrade",
        "tun",
    } <= plan.jobs
    assert not ci.select(["src/solaris/ui/Main.qml"]).protocol


def test_a_code_change_after_hundreds_of_docs_is_not_truncated() -> None:
    paths = [f"docs/note-{i}.md" for i in range(350)]
    paths.append("src/core/internal/engine/lifecycle.go")
    assert {"go", "tun", "apple", "windows-installer"} <= ci.select(paths).jobs


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root: Path, path: str, text: str = "content") -> str:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "CI tests")
    git(tmp_path, "config", "user.email", "ci-tests@example.test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    commit(tmp_path, "README.md")
    return tmp_path


def test_push_uses_all_commits_even_when_the_last_commit_only_changes_docs(
    repository: Path,
) -> None:
    before = git(repository, "rev-parse", "HEAD")
    commit(repository, "src/core/internal/engine/engine.go")
    after = commit(repository, "docs/usage.md")
    plan = ci.for_event("push", {"before": before, "after": after}, repository, push_base=before)
    assert {"go", "tun"} <= plan.jobs


def test_pull_request_uses_merge_base_not_unrelated_new_base_changes(repository: Path) -> None:
    git(repository, "checkout", "-qb", "feature")
    head = commit(repository, "docs/usage.md")
    git(repository, "checkout", "-q", "main")
    base = commit(repository, "src/core/internal/engine/engine.go")
    event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    assert ci.for_event("pull_request", event, repository).jobs == set()


def test_rename_keeps_the_removed_code_path_and_handles_unusual_names(repository: Path) -> None:
    old = "src/core/internal/engine/engine.go"
    new = "docs/moved code notes.md"
    before = commit(repository, old)
    (repository / "docs").mkdir()
    (repository / old).rename(repository / new)
    after = commit(repository, "docs/another.md")
    event = {"before": before, "after": after}
    paths = ci.changed_paths("push", event, repository, push_base=before)
    assert old in paths
    assert new in paths
    assert "go" in ci.for_event("push", event, repository, push_base=before).jobs


@pytest.mark.parametrize("before", ["0" * 40, "f" * 40, "--output=unexpected", ""])
def test_missing_or_invalid_history_falls_back_to_full(repository: Path, before: str) -> None:
    plan = ci.for_event(
        "push",
        {"before": before, "after": git(repository, "rev-parse", "HEAD")},
        repository,
        push_base=before,
    )
    assert plan.jobs == ci.JOBS
    assert plan.protocol


def test_manual_and_unknown_events_are_full_runs(tmp_path: Path) -> None:
    assert ci.for_event("workflow_dispatch", {}, tmp_path).jobs == ci.JOBS
    assert ci.for_event("unknown", {}, tmp_path).jobs == ci.JOBS


def workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_wires_every_job_and_preserves_a_full_run_entry() -> None:
    config = workflow()
    jobs = config["jobs"]
    assert "workflow_dispatch" in config["on"]
    assert set(jobs) == ci.JOBS | {"changes", "result"}
    assert jobs["changes"]["outputs"] == {
        "jobs": "${{ steps.select.outputs.jobs }}",
        "protocol": "${{ steps.select.outputs.protocol }}",
    }
    assert set(jobs["result"]["needs"]) == ci.JOBS | {"changes"}
    assert jobs["result"]["if"] == "always()"
    for name in ci.JOBS:
        needs = jobs[name]["needs"]
        assert "changes" in ([needs] if isinstance(needs, str) else needs)
        assert jobs[name]["if"] == f"contains(fromJSON(needs.changes.outputs.jobs), '{name}')"
    assert "windows-installer" in jobs["windows-arm64-upgrade"]["needs"]


@pytest.mark.parametrize(
    ("selected", "overrides", "success"),
    [
        ([], {}, True),
        (["python"], {"python": "success"}, True),
        (["python"], {"python": "skipped"}, False),
        (["python"], {"python": "failure"}, False),
        (["python"], {"python": "cancelled"}, False),
        ([], {"changes": "failure"}, False),
        ([], {"go": "failure"}, False),
        ([], {"go": "timed_out"}, False),
        (["not-a-job"], {}, False),
    ],
)
def test_actual_summary_step_rejects_missing_and_failed_checks(
    selected: list[str], overrides: dict[str, str], success: bool
) -> None:
    code = workflow()["jobs"]["result"]["steps"][0]["run"].splitlines()
    assert code[0] == "python3 - <<'PY'"
    assert code[-1] == "PY"
    results = {name: {"result": "skipped"} for name in ci.JOBS}
    results["changes"] = {"result": "success"}
    results.update({name: {"result": value} for name, value in overrides.items()})
    result = subprocess.run(
        [sys.executable, "-c", "\n".join(code[1:-1])],
        env={
            **os.environ,
            "CHECK_RESULTS": json.dumps(results),
            "SELECTED_JOBS": json.dumps(selected),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr


def test_bootstrap_needs_no_project_packages_and_emits_valid_outputs(tmp_path: Path) -> None:
    source = Path(ci.__file__)
    ast.parse(source.read_text(), feature_version=(3, 10))
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(source), "--paths", "README.md"],
        env={**os.environ, "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["jobs"] == []
    assert output.read_text() == "jobs=[]\nprotocol=false\n"
    assert "Jobs: none" in summary.read_text()


def test_docs_after_failed_code_still_select_the_unverified_code(repository: Path) -> None:
    successful = git(repository, "rev-parse", "HEAD")
    failed = commit(repository, "src/core/internal/engine/engine.go")
    after = commit(repository, "docs/usage.md")
    event = {"before": failed, "after": after}
    plan = ci.for_event("push", event, repository, push_base=successful)
    assert {"go", "tun", "windows-installer"} <= plan.jobs
    assert plan.base == successful
    assert ci.for_event("push", event, repository).jobs == ci.JOBS


def test_validated_docs_only_push_skips_code_but_removed_readme_does_not(repository: Path) -> None:
    before = git(repository, "rev-parse", "HEAD")
    after = commit(repository, "docs/usage.md")
    assert not ci.for_event(
        "push", {"before": before, "after": after}, repository, push_base=before
    ).jobs
    (repository / "README.md").unlink()
    after = commit(repository, "docs/usage.md", "updated")
    assert (
        ci.for_event("push", {"before": before, "after": after}, repository, push_base=before).jobs
        == ci.JOBS
    )


def successful_run(sha: str, **changes: object) -> dict:
    return {
        "head_sha": sha,
        "head_repository": {"full_name": "owner/repo"},
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "conclusion": "success",
        **changes,
    }


def test_baseline_must_be_successful_same_repository_workflow_and_ancestor(
    repository: Path,
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "-qb", "side")
    unrelated = commit(repository, "other.txt")
    git(repository, "checkout", "-q", "main")
    head = commit(repository, "docs/usage.md")
    invalid = [
        successful_run(unrelated),
        successful_run(base, conclusion="failure"),
        successful_run(base, event="pull_request"),
        successful_run(base, head_branch="other"),
        successful_run(base, path=".github/workflows/release.yml"),
        successful_run(base, head_repository={"full_name": "fork/repo"}),
        successful_run(base, head_repository=None),
    ]
    assert ci.successful_ancestor(invalid, "owner/repo", "main", head, repository) is None
    assert (
        ci.successful_ancestor(
            [*invalid, successful_run(base)], "owner/repo", "main", head, repository
        )
        == base
    )


def test_manual_success_can_be_reused_as_a_baseline(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    assert (
        ci.successful_ancestor(
            [successful_run(head, event="workflow_dispatch")],
            "owner/repo",
            "main",
            head,
            repository,
        )
        == head
    )


@pytest.mark.parametrize("status", [200, 403])
def test_api_baseline_or_full_fallback(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    head = commit(repository, "docs/usage.md")
    event = repository / "event.json"
    event.write_text(json.dumps({"before": base, "after": head}))
    for name, value in {
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF_NAME": "main",
        "GH_TOKEN": "test-token",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    calls: list[tuple] = []

    class Response:
        def __init__(self) -> None:
            self.status = status

        def read(self, limit: int) -> bytes:
            return json.dumps({"workflow_runs": [successful_run(base)]}).encode()

    class Connection:
        def __init__(self, host: str, timeout: int) -> None:
            assert host == "api.github.com"
            assert timeout == 15

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            calls.append((method, path))
            assert headers["Authorization"] == "Bearer test-token"

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            calls.append(("closed",))

    monkeypatch.setattr(ci.http.client, "HTTPSConnection", Connection)
    if status == 200:
        assert ci.previous_success(head, repository) == base
    else:
        # Exercise the real API error path through the command-line entry point.
        monkeypatch.setattr(sys, "argv", ["ci_changes.py"])
        ci.main()
        assert set(json.loads(capsys.readouterr().out)["jobs"]) == ci.JOBS
    assert calls[0][0] == "GET"
    assert "status=success" in calls[0][1]
    assert calls[-1] == ("closed",)


def test_manual_rerun_is_full_without_reusing_a_baseline(tmp_path: Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}")
    result = subprocess.run(
        [sys.executable, "-I", "-S", ci.__file__],
        env={
            **os.environ,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    plan = json.loads(result.stdout)
    assert set(plan["jobs"]) == ci.JOBS
    assert plan["reason"] == "manual rerun"


def guard(root: Path, scratch: Path, event: str, selection: object, **env: str) -> list[str]:
    """Runs the workflow's own check on the script's selection; the jobs it lets through."""
    run = next(
        step for step in workflow()["jobs"]["changes"]["steps"] if step.get("id") == "select"
    )
    code = run["run"].splitlines()
    assert code[0] == "python3 - <<'PY'"
    assert code[-1] == "PY"
    path = scratch / "selection.json"
    path.write_text(selection if isinstance(selection, str) else json.dumps(selection))
    output = scratch / "output"
    output.write_text("")
    subprocess.run(
        [sys.executable, "-c", "\n".join(code[1:-1])],
        cwd=root,
        env={
            **os.environ,
            "EVENT": event,
            "PR_BASE": "",
            "BEFORE": "",
            "SELECTION": str(path),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(scratch / "summary"),
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    jobs = json.loads(values["jobs"])
    assert values["protocol"] == ("true" if set(jobs) == ci.JOBS else "false")
    return jobs


def selected(jobs: list[str], base: str | None = None) -> dict[str, object]:
    return {"jobs": jobs, "protocol": False, "reason": "changed inputs", "base": base}


def test_a_changed_selection_script_runs_everything_whatever_it_selects(
    repository: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    scratch = tmp_path_factory.mktemp("guard")
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "-qb", "feature")
    commit(repository, "docs/usage.md")
    assert guard(repository, scratch, "pull_request", selected([]), PR_BASE=base) == []
    commit(repository, "scripts/ci_changes.py", "JOBS = frozenset()")
    jobs = guard(repository, scratch, "pull_request", selected([]), PR_BASE=base)
    assert set(jobs) == ci.JOBS
    assert "Every check runs" in (scratch / "summary").read_text()


def test_a_push_trusts_the_script_only_if_it_matches_its_baseline_and_previous_head(
    repository: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    scratch = tmp_path_factory.mktemp("guard")
    successful = git(repository, "rev-parse", "HEAD")
    before = commit(repository, "src/core/internal/engine/engine.go")
    commit(repository, "docs/usage.md")
    assert guard(repository, scratch, "push", selected(["go"], successful), BEFORE=before) == ["go"]
    # A failed push changed the script; the next one, docs only, still runs everything.
    failed = commit(repository, "scripts/ci_changes.py", "JOBS = frozenset()")
    commit(repository, "docs/usage.md", "more")
    assert set(guard(repository, scratch, "push", selected([], successful), BEFORE=failed)) == (
        ci.JOBS
    )
    head = git(repository, "rev-parse", "HEAD")
    # A push selection always has its baseline; one without runs everything.
    assert set(guard(repository, scratch, "push", selected([]), BEFORE=head)) == ci.JOBS


@pytest.mark.parametrize(
    ("event", "selection"),
    [
        ("workflow_dispatch", selected([])),
        ("pull_request", "not json"),
        ("pull_request", {"jobs": []}),
    ],
)
def test_other_events_and_unreadable_selections_run_everything(
    repository: Path, tmp_path_factory: pytest.TempPathFactory, event: str, selection: object
) -> None:
    base = git(repository, "rev-parse", "HEAD")
    scratch = tmp_path_factory.mktemp("guard")
    assert set(guard(repository, scratch, event, selection, PR_BASE=base)) == ci.JOBS
