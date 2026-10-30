"""Allow ``python -m solaris``."""

from solaris.app import main

if __name__ == "__main__":
    raise SystemExit(main())
