import QtQuick
import QtQuick.Controls.Basic

// icon-button: an icon-only action without a border, on its row's background.
AbstractButton {
    id: control

    property string iconName
    required property string accessibleName

    implicitWidth: 28
    implicitHeight: 28
    padding: 0
    opacity: enabled ? 1 : Theme.disabledOpacity
    Accessible.role: Accessible.Button
    Accessible.name: accessibleName

    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: control.iconName
            size: 16
            color: control.hovered || control.down ? Theme.text : Theme.textMuted
        }
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: control.down ? Theme.surfaceSelected : "transparent"

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: control.visualFocus
        }
    }
}
