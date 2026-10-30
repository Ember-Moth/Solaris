"""Checking for, downloading and verifying an update against a fake GitHub
(docs/release.md §2, §3, §8)."""

from __future__ import annotations

import os
import shutil
import sys
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from solaris.update import download
from solaris.update.releases import (
    CHECKSUMS_NAME,
    ReleaseSource,
    UpdateError,
    check_download_url,
    installer_name,
    is_newer,
    parse_release,
    parse_version,
)
from solaris.update.signature import (
    APPLE_TEAM_ID,
    CODESIGN,
    SPCTL,
    ChecksumOnly,
    MacVerifier,
    WindowsVerifier,
    default_verifier,
    trusted_signer,
)
from solaris.update.updater import Updater
from tests.support.releases import FakeGitHub, FakeVerifier, fake_github

MAC_DMG = "Solaris-1.1.0-macos-arm64.dmg"
SIGNER = "Ember Moth"  # a Windows signer, for the signed path
WINDOWS_EXE = "Solaris-1.1.0-windows-x64-setup.exe"
BODY = b"installer " * 100_000  # a little under 1 MB: several chunks


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with fake_github() as server:
        server.add(MAC_DMG, BODY)
        server.add(WINDOWS_EXE, b"MZ" + BODY)
        server.add_checksums(MAC_DMG, WINDOWS_EXE)
        yield server


def updater(
    github: FakeGitHub,
    tmp_path: Path,
    verifier: FakeVerifier | None = None,
    *,
    current: str = "1.0.0",
    system: str = "darwin",
    machine: str = "arm64",
) -> Updater:
    return Updater(
        tmp_path,
        ReleaseSource(github.latest_url),
        verifier or FakeVerifier(),
        current=current,
        system=system,
        machine=machine,
    )


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("0.2.0", (0, 2, 0)),
        ("v1.10.3", (1, 10, 3)),
        (" v0.1.0 ", (0, 1, 0)),
        ("0.2", None),
        ("v0.2.0-beta.1", None),
        ("0.2.0rc1", None),
        ("latest", None),
    ],
)
def test_parse_version(text: str, version: tuple[int, int, int] | None) -> None:
    assert parse_version(text) == version


def test_versions_compare_as_numbers() -> None:
    assert is_newer("0.10.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.0.9", "0.1.0")
    assert not is_newer("nightly", "0.1.0")
    assert is_newer("0.1.0", "unknown")


@pytest.mark.parametrize(
    ("system", "machine", "name"),
    [
        ("darwin", "arm64", MAC_DMG),
        ("win32", "AMD64", WINDOWS_EXE),
        ("darwin", "x86_64", None),
        ("win32", "ARM64", None),
        ("linux", "x86_64", None),
    ],
)
def test_installer_per_platform(system: str, machine: str, name: str | None) -> None:
    assert installer_name("1.1.0", system, machine) == name


def test_downloads_only_over_https() -> None:
    assert check_download_url("https://github.com/x") == "https://github.com/x"
    assert check_download_url("http://127.0.0.1:8000/x")
    assert check_download_url("http://localhost/x")
    for url in ("http://github.com/x", "file:///etc/passwd", "ftp://127.0.0.1/x"):
        with pytest.raises(UpdateError) as caught:
            check_download_url(url)
        assert caught.value.code == "bad_release"


def test_release_json_tolerates_odd_assets() -> None:
    release = parse_release(
        {
            "tag_name": "v0.3.1",
            "assets": [{"name": "a", "size": 3, "browser_download_url": "u"}, {"name": 1}, 2],
        }
    )
    assert release.version == "0.3.1"
    assert [asset.name for asset in release.assets] == ["a"]
    assert release.page_url == ""
    with pytest.raises(UpdateError, match="bad_release"):
        parse_release({"tag_name": "nightly"})


def test_check_finds_the_newer_release(github: FakeGitHub, tmp_path: Path) -> None:
    check = updater(github, tmp_path).check()
    assert check.newer
    assert check.release is not None
    assert check.release.version == "1.1.0"
    assert check.installer == MAC_DMG
    assert check.release.page_url.endswith("/releases/tag/v1.1.0")
    path, headers = github.requests[0]
    assert path == "/repos/Ember-Moth/Solaris/releases/latest"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["user-agent"].startswith("solaris/")
    assert "authorization" not in headers


def test_check_when_already_current(github: FakeGitHub, tmp_path: Path) -> None:
    assert not updater(github, tmp_path, current="1.1.0").check().newer
    assert not updater(github, tmp_path, current="1.2.0").check().newer


def test_check_without_an_installer_for_this_machine(github: FakeGitHub, tmp_path: Path) -> None:
    check = updater(github, tmp_path, system="linux", machine="x86_64").check()
    assert check.newer
    assert check.installer is None
    del github.files[MAC_DMG]
    assert updater(github, tmp_path).check().installer is None


def test_check_before_the_first_release(github: FakeGitHub, tmp_path: Path) -> None:
    github.status = 404  # GitHub's answer while a repository has no release
    check = updater(github, tmp_path).check()
    assert (check.release, check.newer, check.installer) == (None, False, None)


@pytest.mark.parametrize(("status", "detail"), [(403, "HTTP 403"), (500, "HTTP 500")])
def test_check_reports_http_errors(
    github: FakeGitHub, tmp_path: Path, status: int, detail: str
) -> None:
    github.status = status
    with pytest.raises(UpdateError) as caught:
        updater(github, tmp_path).check()
    assert (caught.value.code, caught.value.message) == ("check_failed", detail)


def test_check_refuses_a_tag_it_cannot_read(github: FakeGitHub, tmp_path: Path) -> None:
    github.tag = "nightly-2026-10-30"
    with pytest.raises(UpdateError) as caught:
        updater(github, tmp_path).check()
    assert caught.value.code == "bad_release"


def test_check_without_a_server(tmp_path: Path) -> None:
    with fake_github() as github:
        url = github.latest_url
    with pytest.raises(UpdateError) as caught:
        Updater(tmp_path, ReleaseSource(url, timeout_s=2), FakeVerifier()).check()
    assert caught.value.code == "check_failed"


def test_download_checks_the_sum_then_the_signature(github: FakeGitHub, tmp_path: Path) -> None:
    verifier = FakeVerifier()
    update = updater(github, tmp_path, verifier)
    seen: list[tuple[int, int]] = []
    order: list[str] = []
    downloaded = update.download(
        update.check(),
        progress=lambda done, total: seen.append((done, total)),
        verifying=lambda: order.append("verifying"),
    )
    assert downloaded.path == tmp_path / "updates" / MAC_DMG
    assert downloaded.path.read_bytes() == BODY
    assert downloaded.signed
    assert verifier.seen == [downloaded.path]
    assert order == ["verifying"]
    assert seen[-1] == (len(BODY), len(BODY))
    assert len(seen) > 1
    assert sorted(p.name for p in update.directory.iterdir()) == [MAC_DMG]
    assert update.ready(downloaded) == downloaded.path


def test_windows_downloads_its_installer(github: FakeGitHub, tmp_path: Path) -> None:
    update = updater(github, tmp_path, system="win32", machine="AMD64")
    downloaded = update.download(update.check())
    assert downloaded.path.name == WINDOWS_EXE
    assert downloaded.path.read_bytes().startswith(b"MZ")


def windows_updater(github: FakeGitHub, tmp_path: Path, signer: str) -> Updater:
    return Updater(
        tmp_path,
        ReleaseSource(github.latest_url),
        default_verifier("win32", signer),
        current="1.0.0",
        system="win32",
        machine="AMD64",
    )


def test_the_verifier_per_platform_and_signer() -> None:
    assert isinstance(default_verifier("darwin"), MacVerifier)
    assert isinstance(default_verifier("darwin", ""), MacVerifier)
    assert isinstance(default_verifier("win32", SIGNER), WindowsVerifier)
    assert isinstance(default_verifier("win32", ""), ChecksumOnly)
    assert default_verifier("linux") is None
    assert [v.signed for v in (MacVerifier(), WindowsVerifier(SIGNER), ChecksumOnly())] == [
        True,
        True,
        False,
    ]


def test_without_a_windows_signer_the_checksum_vouches(github: FakeGitHub, tmp_path: Path) -> None:
    # A build without a signer (D29) takes an unsigned installer whose SHA-256 is the one
    # in the release's SHA256SUMS, and says it is unsigned.
    update = windows_updater(github, tmp_path, "")
    order: list[str] = []
    downloaded = update.download(update.check(), verifying=lambda: order.append("verifying"))
    assert downloaded.path.read_bytes() == b"MZ" + BODY
    assert not downloaded.signed
    assert order == []  # no signature to check
    assert update.ready(downloaded) == downloaded.path
    downloaded.path.write_bytes(b"MZ swapped")
    with pytest.raises(UpdateError) as caught:
        update.ready(downloaded)
    assert caught.value.code == "checksum_mismatch"


@pytest.mark.parametrize("sums", ["missing", "without it", "wrong"])
def test_without_a_windows_signer_the_checksum_is_required(
    github: FakeGitHub, tmp_path: Path, sums: str
) -> None:
    if sums == "missing":
        del github.files[CHECKSUMS_NAME]
    elif sums == "without it":
        github.add_checksums(MAC_DMG)
    else:
        github.add_checksums(MAC_DMG, WINDOWS_EXE, wrong=WINDOWS_EXE)
    update = windows_updater(github, tmp_path, "")
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == ("checksum_mismatch" if sums == "wrong" else "checksum_missing")
    assert not update.directory.exists() or list(update.directory.iterdir()) == []


def test_with_a_windows_signer_an_unsigned_installer_is_refused(
    github: FakeGitHub, tmp_path: Path
) -> None:
    # The path for signed Windows releases: the fake installer carries no signature (and off Windows
    # there is no WinVerifyTrust), so it is refused after its checksum matched.
    update = windows_updater(github, tmp_path, SIGNER)
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "signature_invalid"
    assert list(update.directory.iterdir()) == []


def test_download_clears_earlier_ones(github: FakeGitHub, tmp_path: Path) -> None:
    update = updater(github, tmp_path)
    update.directory.mkdir(parents=True)
    (update.directory / "Solaris-0.1.5-macos-arm64.dmg").write_bytes(b"old")
    (update.directory / "stale.part").write_bytes(b"half")
    update.download(update.check())
    assert sorted(p.name for p in update.directory.iterdir()) == [MAC_DMG]


def test_download_refuses_a_release_without_checksums(github: FakeGitHub, tmp_path: Path) -> None:
    del github.files[CHECKSUMS_NAME]
    update = updater(github, tmp_path)
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "checksum_missing"
    github.add_checksums(WINDOWS_EXE)  # a SHA256SUMS without this installer
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "checksum_missing"


def test_download_refuses_a_wrong_sum(github: FakeGitHub, tmp_path: Path) -> None:
    github.add_checksums(MAC_DMG, wrong=MAC_DMG)
    verifier = FakeVerifier()
    update = updater(github, tmp_path, verifier)
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "checksum_mismatch"
    assert verifier.seen == []
    assert list(update.directory.iterdir()) == []


@pytest.mark.parametrize("declared", [len(BODY) - 1, len(BODY) + 1, 0, 2 * 1024**3])
def test_download_refuses_a_size_other_than_declared(
    github: FakeGitHub, tmp_path: Path, declared: int
) -> None:
    github.declared[MAC_DMG] = declared
    update = updater(github, tmp_path)
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "download_failed"
    assert not any(update.directory.iterdir()) if update.directory.exists() else True


def test_download_can_be_cancelled(github: FakeGitHub, tmp_path: Path) -> None:
    update = updater(github, tmp_path)
    cancelled = threading.Event()
    with pytest.raises(UpdateError) as caught:
        update.download(update.check(), progress=lambda *_: cancelled.set(), cancelled=cancelled)
    assert caught.value.code == "cancelled"
    assert list(update.directory.iterdir()) == []


@pytest.mark.parametrize("code", ["signature_invalid", "signer_mismatch"])
def test_a_rejected_signature_leaves_nothing(github: FakeGitHub, tmp_path: Path, code: str) -> None:
    update = updater(github, tmp_path, FakeVerifier(UpdateError(code, "detail")))
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == code
    assert list(update.directory.iterdir()) == []


def test_no_installer_to_download(github: FakeGitHub, tmp_path: Path) -> None:
    update = updater(github, tmp_path, system="linux", machine="x86_64")
    with pytest.raises(UpdateError) as caught:
        update.download(update.check())
    assert caught.value.code == "no_installer"


def test_ready_checks_the_file_again(github: FakeGitHub, tmp_path: Path) -> None:
    update = updater(github, tmp_path)
    downloaded = update.download(update.check())
    downloaded.path.write_bytes(b"swapped")
    with pytest.raises(UpdateError) as caught:
        update.ready(downloaded)
    assert caught.value.code == "checksum_mismatch"
    downloaded.path.unlink()
    with pytest.raises(UpdateError) as caught:
        update.ready(downloaded)
    assert caught.value.code == "download_failed"


def test_parse_checksums() -> None:
    text = (
        f"{'A' * 64}  Solaris-0.2.0-macos-arm64.dmg\n"
        f"{'b' * 64} *Solaris-0.2.0-windows-x64-setup.exe\r\n"
        "not a line\n"
        f"{'c' * 63}  short\n"
    )
    assert download.parse_checksums(text) == {
        "Solaris-0.2.0-macos-arm64.dmg": "a" * 64,
        "Solaris-0.2.0-windows-x64-setup.exe": "b" * 64,
    }


# -- signatures -------------------------------------------------------------------------------


class Tools:
    """codesign and spctl as a test wants them to answer."""

    def __init__(self, team: str = APPLE_TEAM_ID, failing: str = "") -> None:
        self.team = team
        self.failing = failing
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> tuple[int, str]:
        self.commands.append(list(command))
        tool, action = command[0], command[1]
        if action == self.failing:
            return 3, f"{command[-1]}: rejected\nsource=Unnotarized Developer ID\n"
        if tool == CODESIGN and action == "--display":
            return 0, f"Executable={command[-1]}\nTeamIdentifier={self.team}\n"
        return 0, ""


def test_mac_verifier_runs_the_three_checks(tmp_path: Path) -> None:
    tools = Tools()
    MacVerifier(run=tools).verify(tmp_path / "a.dmg")
    target = str(tmp_path / "a.dmg")
    assert tools.commands == [
        [CODESIGN, "--verify", "--deep", "--strict", "--verbose=2", target],
        [CODESIGN, "--display", "--verbose=2", target],
        [
            SPCTL,
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            "--verbose=2",
            target,
        ],
    ]


@pytest.mark.parametrize(
    ("tools", "code", "detail"),
    [
        (Tools(failing="--verify"), "signature_invalid", "source=Unnotarized Developer ID"),
        (Tools(team="not set"), "signer_mismatch", "TeamIdentifier=not set"),
        (Tools(team="ABCDE12345"), "signer_mismatch", "TeamIdentifier=ABCDE12345"),
        (Tools(failing="--assess"), "signature_invalid", "source=Unnotarized Developer ID"),
    ],
)
def test_mac_verifier_rejects(tmp_path: Path, tools: Tools, code: str, detail: str) -> None:
    with pytest.raises(UpdateError) as caught:
        MacVerifier(run=tools).verify(tmp_path / "a.dmg")
    assert (caught.value.code, caught.value.message) == (code, detail)


@pytest.mark.skipif(sys.platform != "darwin", reason="codesign is on macOS")
def test_mac_verifier_with_the_real_tools(tmp_path: Path) -> None:
    # Apple's own apps are signed, but not by this team.
    with pytest.raises(UpdateError) as caught:
        MacVerifier().verify(Path("/System/Applications/Calculator.app"))
    assert caught.value.code == "signer_mismatch"
    unsigned = tmp_path / "unsigned.dmg"
    unsigned.write_bytes(b"not a disk image")
    with pytest.raises(UpdateError) as caught:
        MacVerifier().verify(unsigned)
    assert caught.value.code == "signature_invalid"
    assert "not signed" in caught.value.message


@pytest.mark.skipif(sys.platform != "win32", reason="WinVerifyTrust is on Windows")
def test_windows_verifier_rejects_an_unsigned_file(tmp_path: Path) -> None:
    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(b"MZ" + bytes(1024))
    with pytest.raises(UpdateError) as caught:
        WindowsVerifier(SIGNER).verify(unsigned)
    assert caught.value.code == "signature_invalid"
    assert caught.value.message.startswith("0x800")


def _signed_by_microsoft() -> Path | None:
    """A program with an embedded Authenticode signature; system files are mostly signed
    through catalogs, which WinVerifyTrust on the file alone does not see."""
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    candidates = [
        program_files / "PowerShell" / "7" / "pwsh.exe",
        program_files / "Microsoft VS Code" / "Code.exe",
        program_files / "Git" / "cmd" / "git.exe",
    ]
    if found := shutil.which("pwsh"):
        candidates.insert(0, Path(found))
    return next((path for path in candidates if path.is_file()), None)


@pytest.mark.skipif(sys.platform != "win32", reason="WinVerifyTrust is on Windows")
def test_windows_verifier_names_the_signer() -> None:
    program = _signed_by_microsoft()
    if program is None:
        pytest.skip("no program with an embedded signature on this machine")
    signer = trusted_signer(program)
    assert signer
    with pytest.raises(UpdateError) as caught:
        WindowsVerifier(SIGNER).verify(program)
    assert (caught.value.code, caught.value.message) == ("signer_mismatch", signer)
    WindowsVerifier(signer=signer).verify(program)
