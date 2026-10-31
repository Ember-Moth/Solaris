"""Install crash capture before importing Qt or the composition root."""

from solaris.crashes import Recorder
from solaris.models.store import default_data_dir


def main() -> int:
    recorder = Recorder(default_data_dir() / "crashes").install()
    try:
        from solaris.app import main as application

        return application()
    except BaseException as error:
        recorder.record(error, origin="main")
        raise
    finally:
        recorder.close()
