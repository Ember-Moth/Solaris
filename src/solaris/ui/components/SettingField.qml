import QtQuick

// input-compact for a setting that is saved as it is typed (DESIGN.md "输入框"), such as the
// mixed port: a click goes into the field; Enter, Tab or a click elsewhere in the window
// commits the text; Esc puts the saved `value` back and leaves the field. The field shows
// `value` again whenever it changes. Its own edit menu keeps it editing.
AppTextField {
    id: field

    property string value

    // The text to save: the page checks it, and keeps it or says why not under the label.
    signal committed(string text)

    // Only a change is sent, or the text of a field that shows an error, so that the page
    // can check it again and take the error away.
    function commit() {
        if (text !== value || error)
            committed(text)
    }

    implicitHeight: Theme.controlSm
    leftPadding: Theme.space2
    rightPadding: Theme.space2
    Component.onCompleted: text = value
    onValueChanged: text = value
    onAccepted: commit()
    onActiveFocusChanged: {
        if (!activeFocus && !editMenuOpen)
            commit()
    }
    onEditMenuOpenChanged: {
        if (!editMenuOpen && !activeFocus)
            commit()
    }
    Keys.onEscapePressed: {
        text = value
        commit()
        focus = false
    }
}
