import QtQuick
import QtQuick.Controls.Basic

// input: a white field with the border-control outline and body-sm text; `error` turns the
// outline danger, and the caller writes the error under the field.
TextField {
    id: control

    property bool error

    implicitHeight: Theme.controlMd
    leftPadding: 10
    rightPadding: 10
    topPadding: 0
    bottomPadding: 0
    verticalAlignment: TextInput.AlignVCenter
    color: Theme.text
    placeholderTextColor: Theme.textMuted
    selectionColor: Theme.primary
    selectedTextColor: Theme.textOnPrimary
    font.family: Fonts.resolve(Theme.type.bodySm.fontFamily)
    font.pixelSize: Theme.type.bodySm.fontSize
    font.weight: Theme.type.bodySm.fontWeight
    opacity: enabled ? 1 : Theme.disabledOpacity

    background: Rectangle {
        implicitWidth: 160
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: 1
        border.color: control.error ? Theme.danger : Theme.borderControl

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: control.activeFocus
        }
    }
}
