import QtQuick
import QtQuick.Layouts

// One row inside a Group: a label on the left, a value or control on the right.
Item {
    id: row

    property bool divider: true
    property int rowHeight: Theme.rowDouble
    property int verticalPadding: 0
    default property alias content: inner.data

    Layout.fillWidth: true
    implicitHeight: Math.max(rowHeight, inner.implicitHeight + 2 * verticalPadding)

    Rectangle {
        visible: row.divider
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: Theme.divider
    }

    RowLayout {
        id: inner
        anchors.fill: parent
        anchors.leftMargin: Theme.space4
        anchors.rightMargin: Theme.space4
        anchors.topMargin: row.verticalPadding
        anchors.bottomMargin: row.verticalPadding
        spacing: Theme.space3
    }
}
