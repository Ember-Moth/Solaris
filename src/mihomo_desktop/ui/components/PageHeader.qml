import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The one-line page header: title, a short status, actions, then header-rule.
ColumnLayout {
    id: header

    property string title
    property string detail
    property color detailColor: Theme.textMuted
    // The undo hint takes the status text's place while it is set (DESIGN.md "撤销提示").
    property string undoText
    property string undoName
    default property alias actions: actionRow.data
    signal undoRequested

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
            visible: header.undoText === ""
            text: header.detail
            role: Theme.type.bodySm
            color: header.detailColor
            elide: Text.ElideRight
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            visible: header.undoText !== ""
            spacing: 0
            Accessible.role: Accessible.StaticText
            Accessible.name: header.undoText

            UiText {
                Layout.preferredWidth: Math.ceil(implicitWidth)
                Layout.maximumWidth: Math.ceil(implicitWidth)
                Layout.fillWidth: true
                text: qsTr("%1 · ").arg(header.undoText)
                role: Theme.type.bodySm
                color: Theme.textSecondary
                elide: Text.ElideRight
            }

            AbstractButton {
                id: undoButton

                padding: 0
                Accessible.role: Accessible.Button
                Accessible.name: header.undoName
                onClicked: header.undoRequested()

                contentItem: UiText {
                    text: qsTr("撤销")
                    role: Theme.type.bodySm
                    color: undoButton.hovered ? Theme.linkHover : Theme.link
                }

                background: Item {
                    FocusRing {
                        targetRadius: 2
                        visible: undoButton.visualFocus
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
            }
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
