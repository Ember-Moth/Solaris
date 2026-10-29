pragma Singleton
import QtQuick

// Maps DESIGN.md font families to installed ones. Inter and JetBrains Mono are not bundled
// yet, so the system UI and monospace fonts stand in when they are missing.
QtObject {
    readonly property var installed: Qt.fontFamilies()

    function resolve(family: string): string {
        if (installed.indexOf(family) >= 0)
            return family
        if (family === "JetBrains Mono")
            return Qt.platform.os === "osx" ? "Menlo" : Qt.platform.os === "windows" ? "Consolas" : "monospace"
        return Qt.application.font.family
    }
}
