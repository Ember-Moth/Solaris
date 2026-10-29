import QtQuick

// Text in one of DESIGN.md's type roles (Theme.type.*).
Text {
    property var role: Theme.type.bodyMd

    color: Theme.text
    textFormat: Text.PlainText
    font.family: Fonts.resolve(role.fontFamily)
    font.pixelSize: role.fontSize
    font.weight: role.fontWeight
    lineHeightMode: Text.ProportionalHeight
    lineHeight: wrapMode === Text.NoWrap ? 1.0 : role.lineHeight
}
