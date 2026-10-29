"""Ask a running control service for GetInfo.

On Windows this talks to the installed service pipe. Elsewhere, point it at the
development server started with:

    go -C src/core run ./service -socket /tmp/mihomo-desktop-dev.sock
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from mihomo_desktop.host.base import HostError
from mihomo_desktop.host.service import ServiceHost
from mihomo_desktop.host.transport import Opener
from mihomo_desktop.product import PIPE_NAME


def opener(socket: str | None) -> Opener:
    if sys.platform == "win32":
        from mihomo_desktop.host.transport import pipe_opener

        if socket:
            raise SystemExit("--socket only applies to the development server outside Windows")
        return pipe_opener(PIPE_NAME)
    from mihomo_desktop.host.transport import unix_opener

    if not socket:
        raise SystemExit("--socket is required outside Windows")
    return unix_opener(socket)


async def ping(target: Opener) -> None:
    host = ServiceHost(target)
    try:
        print(await host.get_info())
    finally:
        await host.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", help="development socket path")
    args = parser.parse_args()
    try:
        asyncio.run(ping(opener(args.socket)))
    except HostError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
