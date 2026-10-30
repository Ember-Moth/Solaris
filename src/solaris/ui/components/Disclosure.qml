import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// disclosure (DESIGN.md "可折叠区"): a section that folds away under a text button with a
// chevron, which turns down while it is open. A divider above separates it from what comes
// before; the content follows the button 12px below.
ColumnLayout {
    id: root

    property string text
    property bool open
    default property alias content: body.data
    signal toggled(bool open)

    spacing: Theme.space3

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.divider
    }

    AbstractButton {
        id: button

        Layout.fillWidth: true
        implicitHeight: Theme.controlSm
        padding: 0
        Accessible.role: Accessible.Button
        Accessible.name: root.text
        Accessible.description: root.open ? qsTr("已展开") : qsTr("已收起")
        onClicked: root.toggled(!root.open)

        contentItem: RowLayout {
            spacing: 6

            Icon {
                name: "chevron"
                size: 14
                rotation: root.open ? 90 : 0

                Behavior on rotation {
                    NumberAnimation {
                        duration: Motion.stateChange
                        easing.type: Easing.OutCubic
                    }
                }
            }

            UiText {
                Layout.fillWidth: true
                text: root.text
                role: Theme.type.labelSm
                elide: Text.ElideRight
            }
        }

        background: Item {
            FocusRing {
                targetRadius: 4
                visible: button.visualFocus
            }
        }
    }

    ColumnLayout {
        id: body

        Layout.fillWidth: true
        visible: root.open
        spacing: Theme.space4
    }
}
