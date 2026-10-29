"""Run the UI against the simulated host and sample profile from tests/support.

    uv run python scripts/preview.py

Source-tree only: the preview's test doubles never ship in the package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihomo_desktop.app import main
from tests.support.simulated import SampleProfiles, SimulatedHost

if __name__ == "__main__":
    raise SystemExit(main(SimulatedHost(health_check_s=0.8), SampleProfiles()))
