import QtQuick
import QtQuick.Controls.Basic

// A table-header cell that sorts its column. The sorted column's label turns text-coloured
// and an arrow points the way the values run: down for largest first. In a right-aligned
// column the arrow goes before the label, so the label lines up with the values.
AbstractButton {
    id: control

    property bool sorted
    property bool descending: true
    property int alignment: Qt.AlignLeft
    // False while the column is scrolled out of view: Qt's software scene graph draws a
    // Shape that lies wholly outside its clip anyway.
    property bool arrowShown: true

    implicitHeight: Theme.rowHeader
    padding: 0
    Accessible.role: Accessible.ColumnHeader
    Accessible.name: sorted ? (descending ? qsTr("%1，从大到小").arg(text) : qsTr("%1，从小到大").arg(text)) : text

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight

        Row {
            id: row

            anchors.verticalCenter: parent.verticalCenter
            anchors.right: control.alignment === Qt.AlignRight ? parent.right : undefined
            layoutDirection: control.alignment === Qt.AlignRight ? Qt.RightToLeft : Qt.LeftToRight
            spacing: Theme.space1

            UiText {
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                role: Theme.type.caption
                color: control.sorted || control.hovered ? Theme.text : Theme.textMuted
            }

            Icon {
                anchors.verticalCenter: parent.verticalCenter
                visible: control.sorted
                opacity: control.arrowShown ? 1 : 0
                name: control.descending ? "arrowDown" : "arrowUp"
                size: 12
                color: Theme.text
            }
        }
    }

    background: Item {
        FocusRing {
            anchors.margins: -2
            targetRadius: Theme.radiusXs
            visible: control.visualFocus
        }
    }
}
