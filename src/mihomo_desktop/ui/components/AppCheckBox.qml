import QtQuick
import QtQuick.Controls.Basic

// A form choice that applies when the form is submitted; settings that apply at once use
// AppSwitch. Drawn like the radio ring: a border-control outline, filled primary when on.
CheckBox {
    id: control

    padding: 0
    spacing: Theme.space2
    opacity: enabled ? 1 : Theme.disabledOpacity
    Accessible.name: text

    indicator: Rectangle {
        implicitWidth: 16
        implicitHeight: 16
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: Theme.radiusXs
        color: control.checked ? Theme.primary : Theme.surface
        border.width: control.checked ? 0 : 1.5
        border.color: Theme.borderControl

        Icon {
            anchors.centerIn: parent
            visible: control.checked
            name: "check"
            size: 12
            color: Theme.textOnPrimary
        }

        FocusRing {
            targetRadius: Theme.radiusXs
            visible: control.visualFocus
        }
    }

    contentItem: UiText {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        role: Theme.type.bodySm
        color: Theme.textSecondary
        verticalAlignment: Text.AlignVCenter
    }
}
