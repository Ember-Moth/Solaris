pragma ComponentBehavior: Bound
import QtQuick

// Gives a text field or editor the app's edit menu (EditMenu) in place of Qt's. Declared
// inside the editor, whose ContextMenu.onRequested calls open(). A right-click opens the
// menu at the pointer; Qt itself would put it at the text cursor, which is where the
// keyboard's menu key, coming without a pointer, still opens it. The editor takes the focus
// first, as a native field does, and gets it back when the menu closes. The menu is made the
// first time it is asked for.
Item {
    id: host

    required property Item editor
    property EditMenu menu: null
    readonly property bool opened: menu?.visible ?? false

    function open(position: point) {
        if (!menu)
            menu = menuComponent.createObject(editor) as EditMenu
        editor.forceActiveFocus(Qt.MouseFocusReason)
        menu.openAtPointer(editor, pointer.hovered ? pointer.point.position : position)
    }

    anchors.fill: parent

    HoverHandler {
        id: pointer
    }

    Component {
        id: menuComponent

        EditMenu {
            editor: host.editor
        }
    }
}
