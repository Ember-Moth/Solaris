pragma Singleton
import QtQuick

// Maps DESIGN.md font families to the fonts bundled in ../fonts: Inter 400, 500 and 600 and
// JetBrains Mono 400, each with its OFL.txt. Qt takes the glyphs they lack, such as Chinese,
// from the system fonts (DESIGN.md "Typography"). If a bundled family fails to load, an
// installed copy or the system UI and monospace fonts stand in.
QtObject {
    readonly property list<FontLoader> bundled: [
        FontLoader {
            source: "../fonts/inter/Inter-Regular.ttf"
        },
        FontLoader {
            source: "../fonts/inter/Inter-Medium.ttf"
        },
        FontLoader {
            source: "../fonts/inter/Inter-SemiBold.ttf"
        },
        FontLoader {
            source: "../fonts/jetbrains-mono/JetBrainsMono-Regular.ttf"
        }
    ]
    readonly property var installed: Qt.fontFamilies()

    function resolve(family: string): string {
        for (let i = 0; i < bundled.length; i++) {
            if (bundled[i].status === FontLoader.Ready && bundled[i].name === family)
                return family
        }
        if (installed.indexOf(family) >= 0)
            return family
        if (family === "JetBrains Mono")
            return Qt.platform.os === "osx" ? "Menlo" : Qt.platform.os === "windows" ? "Consolas" : "monospace"
        return Application.font.family
    }
}
