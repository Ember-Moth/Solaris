pragma Singleton
import QtQuick

// Durations from DESIGN.md "Components → 动效", in ms. All of them are 0 while the system
// asks for reduced motion, so state changes and overlays switch at once.
QtObject {
    readonly property bool reduced: Application.styleHints.accessibility.motionPreference === Qt.ReducedMotion

    readonly property int stateChange: reduced ? 0 : 120  // switch thumb, segmented selection
    readonly property int popupIn: reduced ? 0 : 120  // menus and popovers
    readonly property int popupOut: reduced ? 0 : 80
    readonly property int dialogIn: reduced ? 0 : 160
    readonly property int dialogOut: reduced ? 0 : 100
}
