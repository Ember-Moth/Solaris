import QtQuick
import QtQuick.Layouts

// The one-line page header: title, a short status, actions, then header-rule.
ColumnLayout {
    id: header

    property string title
    property string detail
    property color detailColor: Theme.textMuted
    default property alias actions: actionRow.data

    spacing: Theme.space4

    RowLayout {
        Layout.fillWidth: true
        Layout.minimumHeight: Theme.controlMd
        spacing: Theme.space3

        UiText {
            text: header.title
            role: Theme.type.title
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        AccessibleText {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            text: header.detail
            role: Theme.type.bodySm
            color: header.detailColor
            elide: Text.ElideRight
        }

        RowLayout {
            id: actionRow
            spacing: Theme.space2
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.border
    }
}
