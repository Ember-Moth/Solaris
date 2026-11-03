pragma Singleton
import QtQuick

// fonts.py registers the bundled families and script/emoji fallbacks before QML loads.
// This fallback only keeps the interface usable after a diagnosed missing font asset.
QtObject {
    readonly property var installed: Qt.fontFamilies()

    function resolve(family: string): string {
        if (installed.indexOf(family) >= 0)
            return family
        if (family === "JetBrains Mono")
            return Qt.platform.os === "osx" ? "Menlo" : Qt.platform.os === "windows" ? "Consolas" : "monospace"
        return Application.font.family
    }
}
