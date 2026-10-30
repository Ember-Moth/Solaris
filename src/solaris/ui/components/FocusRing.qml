import QtQuick

// The keyboard focus ring: 2px primary, 2px outside the control (DESIGN.md "Components").
Rectangle {
    property real targetRadius: 0

    anchors.fill: parent
    anchors.margins: -4
    radius: targetRadius + 4
    color: "transparent"
    border.width: 2
    border.color: Theme.primary
}
