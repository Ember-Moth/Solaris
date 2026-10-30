import QtQuick
import QtQuick.Controls.Basic

// switch-on / switch-off. `value` follows the ViewModel; a click asks for the other value
// through `requested` and the switch returns to `value` until the ViewModel confirms it.
Switch {
    id: control

    property bool value
    signal requested(bool on)

    checked: value
    implicitWidth: 40
    implicitHeight: 24
    padding: 0
    spacing: 0
    opacity: enabled ? 1 : Theme.disabledOpacity
    onToggled: {
        requested(checked)
        checked = Qt.binding(() => control.value)
    }

    indicator: Rectangle {
        implicitWidth: 40
        implicitHeight: 24
        x: 0
        y: (control.height - height) / 2
        radius: height / 2
        color: control.checked ? Theme.primary : Theme.borderControl

        Rectangle {
            width: 18
            height: 18
            radius: 9
            y: 3
            x: control.checked ? parent.width - width - 3 : 3
            color: Theme.surface

            Behavior on x {
                NumberAnimation {
                    duration: Motion.stateChange
                    easing.type: Easing.OutCubic
                }
            }
        }

        FocusRing {
            targetRadius: 12
            visible: control.visualFocus
        }
    }

    contentItem: Item {}
}
