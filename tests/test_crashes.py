from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from solaris import crashes


def probe(directory: Path, code: str) -> subprocess.CompletedProcess[str]:
    script = (
        """
import asyncio, os, sys, threading
from pathlib import Path
from solaris.crashes import Recorder, asyncio_exception
if sys.platform != "win32":
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
else:
    # Keep deliberate faults inside this child: no error dialog or OS crash-report UI.
    import ctypes
    ctypes.WinDLL("kernel32").SetErrorMode(0x0002 | 0x8000)
    ctypes.CDLL("ucrtbase")._set_abort_behavior(0, 3)
recorder = Recorder(Path(sys.argv[1])).install()
"""
        + code
    )
    return subprocess.run(
        [sys.executable, "-c", script, str(directory)],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ('raise RuntimeError("main failure")', "main failure"),
        (
            "thread = threading.Thread(target=lambda: 1/0)\n"
            "thread.start()\nthread.join()\nrecorder.close()",
            "ZeroDivisionError",
        ),
        (
            """
loop = asyncio.new_event_loop()
loop.set_exception_handler(asyncio_exception)
loop.call_soon(lambda: 1/0)
loop.run_until_complete(asyncio.sleep(0.01))
loop.close()
recorder.close()
""",
            "ZeroDivisionError",
        ),
        ("os.abort()", "Fatal Python error"),
    ],
)
def test_uncaught_failures_are_captured_in_separate_processes(
    tmp_path: Path, code: str, text: str
) -> None:
    probe(tmp_path, code)
    inventory = crashes.collect(tmp_path, sources=[(tmp_path, "ui")])
    assert inventory.reports
    raw = "\n".join(report.path.read_text(errors="replace") for report in inventory.reports)
    assert text in raw
    assert text not in json.dumps(inventory.summary())


def test_normal_exit_and_cancellation_are_not_crashes(tmp_path: Path) -> None:
    result = probe(tmp_path, "recorder.record(asyncio.CancelledError())\nrecorder.close()")
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cannot_write_log_does_not_hide_original_error(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-directory"
    directory.write_text("kept")
    result = probe(directory, 'raise RuntimeError("original failure")')
    assert "original failure" in result.stderr
    assert directory.read_text() == "kept"


def test_retention_and_clear_preserve_unknown_files_and_active_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    for index in range(6):
        path = tmp_path / f"ui-exception-{index}-1.log"
        path.write_bytes(b"x" * 10)
        os.utime(path, (now - index, now - index))
    old = tmp_path / "go-9-1.log"
    old.write_text("old")
    stamp = now - crashes.MAX_AGE_S - 1
    os.utime(old, (stamp, stamp))
    unrelated = tmp_path / "private.txt"
    unrelated.write_text("keep")
    active = tmp_path / "ui-fatal-10-1.log"
    active.touch()
    monkeypatch.setattr(crashes, "MAX_FILES", 3)
    monkeypatch.setattr(crashes, "MAX_BYTES", 20)
    crashes.prune(tmp_path, now=now, active=active)
    assert sorted(path.name for path in tmp_path.glob("ui-exception*.log")) == [
        "ui-exception-0-1.log",
        "ui-exception-1-1.log",
    ]
    assert not old.exists()
    assert active.exists()
    crashes.clear(tmp_path)
    assert unrelated.read_text() == "keep"


def test_export_is_explicit_and_rejects_replaced_sources(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    report = logs / "ui-exception-1-1.log"
    report.write_text("raw private exception data")
    inventory = crashes.collect(logs, sources=[(logs, "ui")])
    assert "private" not in json.dumps(inventory.summary())
    archive = tmp_path / "crashes.zip"
    crashes.write_archive(archive, inventory)
    with zipfile.ZipFile(archive) as file:
        assert file.read("01-ui.log") == report.read_bytes()
        assert json.loads(file.read("summary.json"))["raw_included"]
    # Replacing the source with a directory must fail, leaving the previous export intact.
    report.unlink()
    report.mkdir()
    saved = archive.read_bytes()
    with pytest.raises(OSError, match="regular file"):
        crashes.write_archive(archive, inventory)
    assert archive.read_bytes() == saved
    assert not archive.with_suffix(".zip.part").exists()


def test_native_collection_requires_solaris_identity(tmp_path: Path) -> None:
    (tmp_path / "SolarisUI-1.ips").write_text(
        json.dumps({"bundleID": "dev.tschen.solaris"}) + "\n{}"
    )
    (tmp_path / "SolarisUI-2.ips").write_text(json.dumps({"bundleID": "other.app"}) + "\n{}")
    (tmp_path / "OtherApp-1.ips").write_text("do not read")
    (tmp_path / "SolarisUI-3.crash").write_text("Identifier: dev.tschen.solaris\n")
    inventory = crashes.collect(tmp_path, sources=[(tmp_path, "macos")])
    assert {report.path.name for report in inventory.reports} == {
        "SolarisUI-1.ips",
        "SolarisUI-3.crash",
    }


def test_links_are_not_collected_or_pruned(tmp_path: Path) -> None:
    target = tmp_path / "private.txt"
    target.write_text("keep")
    link = tmp_path / "go-1-1.log"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink permission unavailable")
    crashes.prune(tmp_path)
    assert link.is_symlink()
    assert not crashes.collect(tmp_path, sources=[(tmp_path, "ui")]).reports
    crashes.clear(tmp_path)
    assert target.read_text() == "keep"


def test_wer_retention_is_scoped_to_our_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crashes, "MAX_BYTES", 5)
    ours = tmp_path / "Solaris.exe.10.dmp"
    ours.write_bytes(b"x" * 6)
    other = tmp_path / "Other.exe.10.dmp"
    other.write_bytes(b"x" * 10)
    crashes.prune_native(tmp_path)
    assert not ours.exists()
    assert other.exists()
