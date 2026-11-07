import QtQuick
import QtQuick.Controls.Basic
import Solaris

// input: a white field with the border-control outline and body-sm text; `error` turns the
// outline danger, and the caller writes the error under the field. Right-click opens the
// app's edit menu (DESIGN.md "右键菜单").
TextField {
    id: control

    property bool error
    readonly property bool editMenuOpen: editMenu.opened

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

    Keys.onPressed: event => {
        TextEditing.prepareDeletion(control, event.key, event.modifiers)
        event.accepted = false
    }

    ContextMenu.menu: null
    ContextMenu.onRequested: position => editMenu.open(position)

    EditContextMenu {
        id: editMenu

        editor: control
    }

    background: Rectangle {
        implicitWidth: 160
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: 1
        border.color: control.error ? Theme.danger : Theme.borderControl

        // Its edit menu has the focus while it is open, on the field's behalf.
        FocusRing {
            targetRadius: Theme.radiusSm
            visible: control.activeFocus || control.editMenuOpen
        }
    }
}
