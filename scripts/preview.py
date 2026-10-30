"""Run the UI against the simulated host and the prototype's sample profiles.

    uv run python scripts/preview.py             # the system's language
    uv run python scripts/preview.py --lang en   # or zh; MIHOMO_DESKTOP_LANG works too
    uv run python scripts/preview.py --appearance dark  # or light, system;
                                                        # MIHOMO_DESKTOP_APPEARANCE works too
    uv run python scripts/preview.py --crowd 2000  # a busy core: 2000 more connections
    uv run python scripts/preview.py --development  # refuse TUN and the system proxy
    uv run python scripts/preview.py --plan  # route with my plan, the default template

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
from mihomo_desktop.models.plans import default_plan
from mihomo_desktop.models.routing import MODE_PLAN, RoutingStore
from mihomo_desktop.models.store import ProfileStore
from mihomo_desktop.viewmodels.appearance import MODES
from tests.support.simulated import SimulatedFetcher, SimulatedHost, seed_profiles

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the UI against the simulated host")
    parser.add_argument("--lang", choices=("zh", "en"), default="")
    parser.add_argument("--appearance", choices=MODES, default="")
    parser.add_argument("--crowd", type=int, default=0, help="extra quiet connections")
    parser.add_argument(
        "--development", action="store_true", help="refuse TUN and the system proxy"
    )
    parser.add_argument(
        "--plan", action="store_true", help="route with my plan instead of a profile's rules"
    )
    args, qt_args = parser.parse_known_args()
    sys.argv[1:] = qt_args
    with tempfile.TemporaryDirectory(prefix="mihomo-desktop-preview-") as directory:
        store = ProfileStore(Path(directory))
        seed_profiles(store, time.time())
        if args.plan:
            routing = RoutingStore(store.database)
            plan = default_plan()
            routing.put_plan(plan)
            routing.update_state(mode=MODE_PLAN, plan_id=plan.id)
        host = SimulatedHost(health_check_s=0.8, crowd=args.crowd, development=args.development)
        code = main(
            host,
            store,
            SimulatedFetcher(delay_s=0.8),
            language=args.lang,
            appearance=args.appearance,
        )
    raise SystemExit(code)
