import QtQuick

Rectangle {
    color: Theme.surface
    radius: Theme.radiusMd

    Rectangle {
        z: 1
        anchors.fill: parent
        anchors.margins: -Theme.space1
        radius: Theme.radiusMd + Theme.space1
        color: "transparent"
        border.width: Theme.space1 + 1
        border.color: Theme.background
    }

    Rectangle {
        z: 1
        anchors.fill: parent
        radius: Theme.radiusMd
        color: "transparent"
        border.width: 1
        border.color: Theme.border
    }
}
