import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// menu-item: text after a 14px check slot; checkable items show menu-check when checked,
// destructive ones use danger text. Hover and keyboard highlight share one grey. A `note`
// (menu-item-note) sits at the right; `separatorBefore` draws a menu-separator above the
// item, for lists whose sections come from a model (DESIGN.md "目标下拉框").
MenuItem {
    id: item

    property bool destructive
    property string note
    property bool separatorBefore
    readonly property int separatorSpace: separatorBefore ? 2 * Theme.space1 + 1 : 0

    implicitHeight: Theme.controlSm + separatorSpace
    leftPadding: Theme.space2 + 14 + Theme.space2
    rightPadding: Theme.space2
    topPadding: separatorSpace
    bottomPadding: 0
    topInset: separatorSpace
    opacity: enabled ? 1 : Theme.disabledOpacity
    Accessible.description: note

    indicator: Icon {
        x: Theme.space2
        y: item.separatorSpace + (item.height - item.separatorSpace - height) / 2
        visible: item.checkable && item.checked
        name: "check"
        size: 14
        color: Theme.primary
    }

    arrow: null

    contentItem: RowLayout {
        spacing: Theme.space3

        UiText {
            Layout.fillWidth: true
            text: item.text
            role: Theme.type.bodySm
            color: item.destructive ? Theme.danger : Theme.text
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        UiText {
            visible: item.note !== ""
            text: item.note
            role: Theme.type.caption
            color: Theme.textMuted
            verticalAlignment: Text.AlignVCenter
        }
    }

    background: Rectangle {
        radius: Theme.radiusXs
        color: item.highlighted ? Theme.surfaceSelected : "transparent"
    }

    Rectangle {
        visible: item.separatorBefore
        y: Theme.space1
        width: parent.width
        height: 1
        color: Theme.divider
    }
}
