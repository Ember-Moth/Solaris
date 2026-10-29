"""Allow ``python -m mihomo_desktop``."""

from mihomo_desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
