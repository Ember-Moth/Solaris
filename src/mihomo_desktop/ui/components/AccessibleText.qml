import QtQuick

// Text that screen readers read out: a status, an error, an empty state or a value. Qt Quick
// leaves plain text out of the accessibility tree; a control's own label needs no copy here.
UiText {
    Accessible.role: Accessible.StaticText
    Accessible.name: text
}
