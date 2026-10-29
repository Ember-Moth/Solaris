"""Run the UI against the simulated host and the prototype's sample profiles.

    uv run python scripts/preview.py                 # the system's language
    uv run python scripts/preview.py --lang en       # or zh; MIHOMO_DESKTOP_LANG works too
    uv run python scripts/preview.py --development   # refuse TUN and the system proxy

Profiles live in a temporary directory and links resolve to sample data, so the preview
touches neither the real profiles nor the network. Source-tree only: the preview's test
doubles never ship in the package.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihomo_desktop.app import main
from mihomo_desktop.models.store import ProfileStore
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the UI against the simulated host")
    parser.add_argument("--lang", choices=("zh", "en"), default="")
    parser.add_argument(
        "--development", action="store_true", help="refuse TUN and the system proxy"
    )
    args, qt_args = parser.parse_known_args()
    sys.argv[1:] = qt_args
    with tempfile.TemporaryDirectory(prefix="mihomo-desktop-preview-") as directory:
        store = ProfileStore(Path(directory))
        seed_profiles(store, time.time())
        host = SimulatedHost(health_check_s=0.8, development=args.development)
        code = main(host, store, SimulatedFetcher(delay_s=0.8), language=args.lang)
    raise SystemExit(code)
