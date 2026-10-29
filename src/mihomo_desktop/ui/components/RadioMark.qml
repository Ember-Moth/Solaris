import QtQuick

// The radio ring of a selectable row; the row itself carries the accessible state.
Rectangle {
    property bool checked

    implicitWidth: 18
    implicitHeight: 18
    radius: width / 2
    color: Theme.surface
    border.width: 1.5
    border.color: checked ? Theme.primary : Theme.borderControl
    Accessible.ignored: true

    Rectangle {
        anchors.centerIn: parent
        visible: parent.checked
        width: 8
        height: 8
        radius: 4
        color: Theme.primary
    }
}
