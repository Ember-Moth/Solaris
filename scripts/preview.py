"""Run the UI against the simulated host and the prototype's sample profiles.

    uv run python scripts/preview.py

Profiles live in a temporary directory and links resolve to sample data, so the preview
touches neither the real profiles nor the network. Source-tree only: the preview's test
doubles never ship in the package.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihomo_desktop.app import main
from mihomo_desktop.models.store import ProfileStore
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="mihomo-desktop-preview-") as directory:
        store = ProfileStore(Path(directory))
        seed_profiles(store, time.time())
        code = main(SimulatedHost(health_check_s=0.8), store, SimulatedFetcher(delay_s=0.8))
    raise SystemExit(code)
