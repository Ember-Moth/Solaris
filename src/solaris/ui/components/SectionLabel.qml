import QtQuick

UiText {
    role: Theme.type.sectionLabel
    color: Theme.textMuted
    elide: Text.ElideRight  // a section named after a proxy group
    Accessible.role: Accessible.Heading
    Accessible.name: text
}
