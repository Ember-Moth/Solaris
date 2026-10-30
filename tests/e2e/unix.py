"""The development service's Unix socket. The end-to-end tests never run on Windows
(tests/conftest.py leaves them out there), where the transport has no Unix opener."""

from __future__ import annotations

import sys

from solaris.host.transport import Opener

if sys.platform == "win32":

    def unix_opener(path: str) -> Opener:
        raise NotImplementedError(f"no Unix socket on Windows: {path}")

else:
    from solaris.host.transport import unix_opener

__all__ = ["unix_opener"]
