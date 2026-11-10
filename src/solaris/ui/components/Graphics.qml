pragma Singleton
import QtQuick

// The shapes drawn with Qt Quick's curve renderer: Icon, CaptionButtons and TrafficChart. Once
// the window has given its graphics back while it hides (Main.qml's release, desktop/memory.py),
// a Shape that drew before draws nothing on the scene graph Qt makes as the window shows again:
// the curve renderer makes the nodes of a path only when the path changes. A new Shape draws, so
// each sits in a Loader active while `shapes` is, and renew() makes them all again.
QtObject {
    property bool shapes: true  // false only for a moment in renew()

    function renew() {
        shapes = false
        shapes = true
    }
}
