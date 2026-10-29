import QtQuick
import QtQuick.Controls.Basic

// menu-separator: a divider line with 4px above and below, set before destructive items.
MenuSeparator {
    leftPadding: 0
    rightPadding: 0
    topPadding: Theme.space1
    bottomPadding: Theme.space1

    contentItem: Rectangle {
        implicitHeight: 1
        color: Theme.divider
    }
}
