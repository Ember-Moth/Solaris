import QtQuick
import QtQuick.Controls.Basic

// One menu row. Checkable items show a check; destructive ones use danger text.
MenuItem {
    id: item

    property bool destructive

    implicitHeight: Theme.controlSm
    leftPadding: Theme.space2 + 14 + Theme.space2
    rightPadding: Theme.space3
    topPadding: 0
    bottomPadding: 0
    opacity: enabled ? 1 : 0.4

    indicator: Icon {
        x: Theme.space2
        y: (item.height - height) / 2
        visible: item.checkable && item.checked
        name: "check"
        size: 14
        color: Theme.primary
    }

    arrow: null

    contentItem: UiText {
        text: item.text
        role: Theme.type.bodySm
        color: item.destructive ? Theme.danger : Theme.text
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: Theme.radiusSm
        color: item.highlighted ? Theme.surfaceSelected : "transparent"
    }
}
