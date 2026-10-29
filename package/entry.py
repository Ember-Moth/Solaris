"""Entry point compiled by Nuitka in standalone mode.

Kept separate from ``mihomo_desktop/__main__.py`` because Nuitka names its output after
the main script. Never build onefile: the Windows service only accepts the UI from its
install directory (docs/architecture.md §5.1).
"""

# Compiled translations; i18n.py looks for them beside the package (docs/architecture.md §7.5).
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/../src/mihomo_desktop/translations/*.qm=mihomo_desktop/translations/

from mihomo_desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
