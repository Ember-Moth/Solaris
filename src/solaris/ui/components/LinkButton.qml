import QtQuick
import QtQuick.Controls.Basic

// A text link to another page, e.g. a section's "管理".
AbstractButton {
    id: control

    padding: 0
    Accessible.role: Accessible.Link
    Accessible.name: text

    contentItem: UiText {
        text: control.text
        role: Theme.type.caption
        color: control.hovered ? Theme.linkHover : Theme.link
    }

    background: Item {
        FocusRing {
            targetRadius: 2
            visible: control.visualFocus
        }
    }
}
