import QtQuick

// input with a search glyph before the text. Esc clears what was typed.
AppTextField {
    id: field

    property string accessibleName: placeholderText
    // Wide enough to show the whole placeholder; pages size the field from it.
    readonly property real placeholderWidth: Math.ceil(placeholderMetrics.advanceWidth) + leftPadding + rightPadding

    leftPadding: 10 + 14 + 6
    Accessible.name: accessibleName
    Keys.onEscapePressed: event => {
        event.accepted = text !== ""
        if (event.accepted) {
            clear()
            textEdited()
        }
    }

    TextMetrics {
        id: placeholderMetrics

        font: field.font
        text: field.placeholderText
    }

    Icon {
        x: 10
        anchors.verticalCenter: parent.verticalCenter
        name: "search"
        size: 14
        color: Theme.textMuted
    }
}
