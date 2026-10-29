pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A dropdown in a settings row (input-compact): the current choice and a chevron. Its list
// is a menu under it, left-aligned and at least as wide, with the current choice checked.
Button {
    id: control

    property var options: []  // [{value, label}]
    property string current
    property string accessibleName
    readonly property string currentLabel: options.find(option => option.value === current)?.label ?? ""
    signal picked(string value)

    implicitHeight: Theme.controlSm
    leftPadding: Theme.space2 + 2
    rightPadding: Theme.space2
    topPadding: 0
    bottomPadding: 0
    opacity: enabled ? 1 : 0.4
    Accessible.role: Accessible.ComboBox
    Accessible.name: accessibleName
    Accessible.description: currentLabel
    onClicked: list.openDropdown(control)

    contentItem: RowLayout {
        spacing: Theme.space2

        UiText {
            Layout.fillWidth: true
            text: control.currentLabel
            role: Theme.type.bodySm
            elide: Text.ElideRight
        }

        Icon {
            name: "chevronDown"
            size: 14
        }
    }

    background: Rectangle {
        implicitWidth: 120
        radius: Theme.radiusSm
        color: control.down ? Theme.surfaceSelected : control.hovered ? Theme.surfaceSubtle : Theme.surface
        border.width: 1
        border.color: Theme.borderControl

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: control.visualFocus
        }
    }

    AppMenu {
        id: list

        minimumWidth: control.width

        Instantiator {
            model: control.options
            onObjectAdded: (index, object) => list.insertItem(index, object)
            onObjectRemoved: (index, object) => list.removeItem(object)

            delegate: AppMenuItem {
                id: choice

                required property var modelData

                text: choice.modelData.label
                checkable: true
                checked: choice.modelData.value === control.current
                onTriggered: {
                    control.picked(choice.modelData.value)
                    // Triggering toggled the check; the current choice decides it again.
                    checked = Qt.binding(() => choice.modelData.value === control.current)
                }
            }
        }
    }
}
