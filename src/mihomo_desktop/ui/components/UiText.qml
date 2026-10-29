import QtQuick

// Text in one of DESIGN.md's type roles (Theme.type.*). Text that wraps has the role's line
// height, which is the font size times the factor as in CSS: Qt's proportional line height
// would scale the font's own, taller line instead. A single line keeps the font's own height,
// so that it centres in its row or control.
Text {
    property var role: Theme.type.bodyMd

    color: Theme.text
    textFormat: Text.PlainText
    font.family: Fonts.resolve(role.fontFamily)
    font.pixelSize: role.fontSize
    font.weight: role.fontWeight
    lineHeightMode: wrapMode === Text.NoWrap ? Text.ProportionalHeight : Text.FixedHeight
    lineHeight: wrapMode === Text.NoWrap ? 1.0 : Math.round(role.fontSize * role.lineHeight)
}
