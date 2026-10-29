import QtQuick
import QtQuick.Controls.Basic

// button-primary, button-secondary, button-compact and button-destructive (confirmation
// dialogs only).
Button {
    id: control

    property string variant: "secondary"
    readonly property bool primary: variant === "primary"
    readonly property bool compact: variant === "compact"
    readonly property bool destructive: variant === "destructive"

    implicitHeight: compact ? Theme.controlSm : Theme.controlMd
    leftPadding: compact ? Theme.space3 : 14
    rightPadding: leftPadding
    topPadding: 0
    bottomPadding: 0
    opacity: enabled ? 1 : 0.4
    font.family: Fonts.resolve(Theme.type.labelMd.fontFamily)
    font.pixelSize: compact ? Theme.type.labelSm.fontSize : Theme.type.labelMd.fontSize
    font.weight: Theme.type.labelMd.fontWeight

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.primary ? Theme.textOnPrimary : control.destructive ? Theme.danger : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        implicitWidth: 56
        radius: Theme.radiusSm
        color: control.primary
            ? (control.down ? Theme.primaryPressed : control.hovered ? Theme.primaryHover : Theme.primary)
            : (control.down ? Theme.surfaceSelected : control.hovered ? Theme.surfaceSubtle : Theme.surface)
        border.width: control.primary ? 0 : 1
        border.color: Theme.borderButton

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: control.visualFocus
        }
    }
}
