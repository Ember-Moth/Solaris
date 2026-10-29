import QtQuick
import QtQuick.Controls.Basic

// A popup menu of AppMenuItems. DESIGN.md has not specified overlays yet, so the menu
// stays flat like a group: white, the group border, no shadow.
Menu {
    padding: Theme.space1

    background: Rectangle {
        implicitWidth: 200
        radius: Theme.radiusMd
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
    }
}
