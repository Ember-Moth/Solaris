import QtQuick
import QtQuick.Controls.Basic

// code-editor: a multi-line monospace text box with the input outline. Long lines scroll
// instead of wrapping; `error` turns the outline danger. Enter starts a new line and
// Ctrl+Enter (⌘↩ on macOS) emits `submitted`. Tab moves focus on, since YAML indents with
// spaces, so the keyboard can still reach the dialog's buttons.
ScrollView {
    id: editor

    property alias text: area.text
    property alias placeholderText: area.placeholderText
    property alias readOnly: area.readOnly
    property bool error

    signal submitted

    implicitHeight: Theme.editorMinHeight
    padding: 1
    Accessible.ignored: true

    function submitOrType(event: KeyEvent) {
        if (event.modifiers & Qt.ControlModifier)
            submitted()
        else
            event.accepted = false
    }

    TextArea {
        id: area

        focus: true
        padding: 10
        color: Theme.text
        placeholderTextColor: Theme.textMuted
        selectionColor: Theme.primary
        selectedTextColor: Theme.textOnPrimary
        font.family: Fonts.resolve(Theme.type.dataMd.fontFamily)
        font.pixelSize: Theme.type.dataMd.fontSize
        font.weight: Theme.type.dataMd.fontWeight
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.NoWrap
        selectByMouse: true
        Accessible.name: editor.Accessible.name
        background: null

        Keys.onReturnPressed: event => editor.submitOrType(event)
        Keys.onEnterPressed: event => editor.submitOrType(event)
        Keys.onTabPressed: nextItemInFocusChain(true).forceActiveFocus(Qt.TabFocusReason)
        Keys.onBacktabPressed: nextItemInFocusChain(false).forceActiveFocus(Qt.BacktabFocusReason)
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: Theme.surface
        border.width: 1
        border.color: editor.error ? Theme.danger : Theme.borderControl

        FocusRing {
            targetRadius: Theme.radiusSm
            visible: area.activeFocus
        }
    }
}
