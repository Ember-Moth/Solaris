"""Install crash capture before importing Qt or the composition root."""

import sys

from solaris.crashes import Recorder
from solaris.models.store import default_data_dir


def main() -> int:
    # The installer's read-only quit probe must not create user data when no UI exists.
    # It does not run the GUI and does not need a new crash sink of its own.
    if "--quit" in sys.argv[1:]:
        from solaris.app import main as application

        return application()
    recorder = Recorder(default_data_dir() / "crashes").install()
    try:
        from solaris.app import main as application

        return application()
    except BaseException as error:
        recorder.record(error, origin="main")
        raise
    finally:
        recorder.close()
