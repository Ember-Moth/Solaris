import QtQuick

// The edit menu of a text field or editor (DESIGN.md "右键菜单"), in place of Qt's: 撤销
// and 重做, then 剪切, 复制 and 粘贴, then 全选, each enabled as the editor's state allows.
// A read-only editor offers 复制 and 全选 only; a password field copies nothing.
AppMenu {
    id: editMenu

    required property Item editor  // a TextInput, such as a TextField, or a TextEdit
    readonly property TextInput input: editor as TextInput
    readonly property TextEdit area: editor as TextEdit
    readonly property bool editable: !(input?.readOnly ?? area?.readOnly ?? true)
    readonly property bool hidden: input !== null && input.echoMode !== TextInput.Normal
    readonly property bool selected: (input?.selectedText ?? area?.selectedText ?? "") !== ""

    AppMenuItem {
        text: qsTr("撤销")
        visible: editMenu.editable
        enabled: visible && (editMenu.input?.canUndo ?? editMenu.area?.canUndo ?? false)
        onTriggered: editMenu.input ? editMenu.input.undo() : editMenu.area?.undo()
    }

    AppMenuItem {
        text: qsTr("重做")
        visible: editMenu.editable
        enabled: visible && (editMenu.input?.canRedo ?? editMenu.area?.canRedo ?? false)
        onTriggered: editMenu.input ? editMenu.input.redo() : editMenu.area?.redo()
    }

    AppMenuSeparator {
        visible: editMenu.editable
        height: visible ? implicitHeight : 0
    }

    AppMenuItem {
        text: qsTr("剪切")
        visible: editMenu.editable
        enabled: visible && editMenu.selected && !editMenu.hidden
        onTriggered: editMenu.input ? editMenu.input.cut() : editMenu.area?.cut()
    }

    AppMenuItem {
        text: qsTr("复制")
        enabled: editMenu.selected && !editMenu.hidden
        onTriggered: editMenu.input ? editMenu.input.copy() : editMenu.area?.copy()
    }

    AppMenuItem {
        text: qsTr("粘贴")
        visible: editMenu.editable
        enabled: visible && (editMenu.input?.canPaste ?? editMenu.area?.canPaste ?? false)
        onTriggered: editMenu.input ? editMenu.input.paste() : editMenu.area?.paste()
    }

    AppMenuSeparator {
        visible: editMenu.editable
        height: visible ? implicitHeight : 0
    }

    AppMenuItem {
        text: qsTr("全选")
        enabled: (editMenu.input?.length ?? editMenu.area?.length ?? 0) > 0
        onTriggered: editMenu.input ? editMenu.input.selectAll() : editMenu.area?.selectAll()
    }
}
