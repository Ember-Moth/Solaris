"""Run the UI against the simulated host and the prototype's sample profiles.

    uv run python scripts/preview.py             # the system's language
    uv run python scripts/preview.py --lang en   # or zh; SOLARIS_LANG works too
    uv run python scripts/preview.py --appearance dark  # or light, system;
                                                        # SOLARIS_APPEARANCE works too
    uv run python scripts/preview.py --crowd 2000  # a busy core: 2000 more connections
    uv run python scripts/preview.py --development  # the development service: neither TUN
                                                    # nor the system proxy
    uv run python scripts/preview.py --takeover settings  # the Windows service's switches;
                                                          # tun is the macOS extension's
    uv run python scripts/preview.py --plan  # route with my plan, the default template
    uv run python scripts/preview.py --update  # a newer release on a fake GitHub

Profiles live in a temporary directory and links resolve to sample data, so the preview
touches neither the real profiles nor the network; updates come from a fake GitHub on a
loopback port, whose installer passes a fake signature check. Source-tree only: the preview's test
doubles never ship in the package.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solaris.app import main
from solaris.host.base import TAKEOVER_NONE, TAKEOVER_SETTINGS, TAKEOVER_TUN
from solaris.models.plans import default_plan
from solaris.models.routing import MODE_PLAN, RoutingStore
from solaris.models.store import ProfileStore
from solaris.update.releases import ReleaseSource
from solaris.update.updater import Updater
from solaris.viewmodels.appearance import MODES
from tests.support.releases import FakeVerifier, sample_github
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
        "--takeover",
        choices=(TAKEOVER_SETTINGS, TAKEOVER_TUN),
        # This platform's host: the macOS extension always runs a TUN.
        default=TAKEOVER_TUN if sys.platform == "darwin" else TAKEOVER_SETTINGS,
        help="what connecting takes over",
    )
    parser.add_argument(
        "--plan", action="store_true", help="route with my plan instead of a profile's rules"
    )
    parser.add_argument("--update", action="store_true", help="offer a newer release")
    args, qt_args = parser.parse_known_args()
    sys.argv[1:] = qt_args
    with (
        tempfile.TemporaryDirectory(prefix="solaris-preview-") as directory,
        sample_github(newer=args.update) as github,
    ):
        store = ProfileStore(Path(directory))
        seed_profiles(store, time.time())
        if args.plan:
            routing = RoutingStore(store.database)
            plan = default_plan()
            routing.put_plan(plan)
            routing.update_state(mode=MODE_PLAN, plan_id=plan.id)
        host = SimulatedHost(
            health_check_s=0.8,
            crowd=args.crowd,
            development=args.development,
            takeover=TAKEOVER_NONE if args.development else args.takeover,
        )
        code = main(
            host,
            store,
            SimulatedFetcher(delay_s=0.8),
            language=args.lang,
            appearance=args.appearance,
            updater=Updater(Path(directory), ReleaseSource(github.latest_url), FakeVerifier()),
        )
    raise SystemExit(code)
