"""Run the UI against the development service, with profiles and settings in a directory
of their own instead of the user data directory.

    uv run python scripts/dev.py                    # a fresh temporary directory
    uv run python scripts/dev.py --data ~/md-dev    # keep the profiles between runs
    uv run python scripts/dev.py --lang en --appearance dark

The script builds the service (go build), starts it as a child process on a socket in a
short temporary directory, keeps mihomo's home under ``<data>/dev-core`` and stops the service
when the UI quits. The home gets the GEO data the installers ship (scripts/fetch_geodata.py)
unless it has some. The service never creates a TUN or changes the system proxy,
routes or DNS (docs/architecture.md §11); "open at login" stays unavailable, as in every run
from source. tests/e2e uses the same DevService.
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "core"
START_TIMEOUT_S = 30.0


def build_service(output: Path) -> Path:
    """``go build`` the service into ``output``; Go's build cache makes repeats cheap."""
    subprocess.run(["go", "-C", str(CORE), "build", "-o", str(output), "./service"], check=True)
    return output


class DevService:
    """The development service as a child process, on ``socket``, with mihomo's home in
    ``home``. ``restart`` keeps both, as the service restarting on its own would."""

    def __init__(self, binary: Path, socket: Path, home: Path, log: Path) -> None:
        self.binary = binary
        self.socket = socket
        self.home = home
        self.log = log
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def pid(self) -> int:
        assert self._process is not None
        return self._process.pid

    def start(self) -> None:
        with self.log.open("ab") as log:
            self._process = subprocess.Popen(
                [str(self.binary), "-socket", str(self.socket), "-home", str(self.home)],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + START_TIMEOUT_S
        while not self.socket.exists():
            if self._process.poll() is not None:
                raise RuntimeError(f"the service exited with {self._process.returncode}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"no socket at {self.socket}")
            time.sleep(0.05)

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def __enter__(self) -> DevService:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


@contextlib.contextmanager
def short_directory() -> Iterator[Path]:
    """A temporary directory for the socket: a Unix socket path holds about 100 bytes."""
    with tempfile.TemporaryDirectory(prefix="md-") as directory:
        yield Path(directory)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from solaris.app import main as run_ui
    from solaris.host.service import ServiceHost

    if sys.platform == "win32":
        raise SystemExit("scripts/dev.py talks to the development service over a Unix socket")
    from scripts.fetch_geodata import seed
    from solaris.host.transport import unix_opener
    from solaris.models.store import ProfileStore
    from solaris.viewmodels.appearance import MODES

    parser = argparse.ArgumentParser(description="Run the UI against the development service")
    parser.add_argument("--data", type=Path, help="profiles and settings (default: temporary)")
    parser.add_argument("--lang", choices=("zh", "en"), default="")
    parser.add_argument("--appearance", choices=MODES, default="")
    args, qt_args = parser.parse_known_args()
    sys.argv[1:] = qt_args
    with contextlib.ExitStack() as stack:
        data = args.data or Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="md-")))
        data.mkdir(parents=True, exist_ok=True)
        run = stack.enter_context(short_directory())
        binary = build_service(run / "service")
        service = DevService(binary, run / "control.sock", data / "dev-core", data / "service.log")
        if not (service.home / "GeoSite.dat").exists():
            try:
                seed(service.home)
            except (OSError, RuntimeError) as error:
                print(f"no GEO data, mihomo downloads it when needed: {error}", file=sys.stderr)
        stack.enter_context(service)
        print(f"data: {data}\nsocket: {service.socket}\nservice log: {service.log}", flush=True)
        host = ServiceHost(unix_opener(str(service.socket)))
        return run_ui(host, ProfileStore(data), language=args.lang, appearance=args.appearance)


if __name__ == "__main__":
    raise SystemExit(main())
