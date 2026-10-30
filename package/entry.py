"""Entry point compiled by Nuitka in standalone mode.

Kept separate from ``solaris/__main__.py`` because Nuitka names its output after
the main script. Never build onefile: the Windows service only accepts the UI from its
install directory (docs/architecture.md §5.1).
"""

# Compiled translations; i18n.py looks for them beside the package (docs/ui.md §5).
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/../src/solaris/translations/*.qm=solaris/translations/
# The QML that app.py loads from ui/, the tray icons, and Inter and JetBrains Mono with their
# OFL.txt, which Fonts.qml loads from ui/fonts/.
# nuitka-project: --include-data-dir={MAIN_DIRECTORY}/../src/solaris/ui=solaris/ui

from solaris.app import main

if __name__ == "__main__":
    raise SystemExit(main())
