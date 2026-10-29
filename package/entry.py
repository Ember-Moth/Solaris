"""Entry point compiled by Nuitka in standalone mode.

Kept separate from ``mihomo_desktop/__main__.py`` because Nuitka names its output after
the main script. Never build onefile: the Windows service only accepts the UI from its
install directory (docs/architecture.md §5.1).
"""

from mihomo_desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
