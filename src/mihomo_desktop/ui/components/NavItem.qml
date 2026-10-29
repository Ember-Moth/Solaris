import QtQuick
import QtQuick.Controls.Basic

// nav-item, nav-item-hover and nav-item-active.
AbstractButton {
    id: item

    property string iconName
    property bool active

    implicitHeight: Theme.rowSingle
    leftPadding: Theme.space3
    rightPadding: Theme.space3
    Accessible.role: Accessible.PageTab
    Accessible.name: text
    Accessible.selected: active

    contentItem: Row {
        spacing: 10

        Icon {
            anchors.verticalCenter: parent.verticalCenter
            name: item.iconName
            color: item.active ? Theme.primary : Theme.textMuted
        }

        UiText {
            anchors.verticalCenter: parent.verticalCenter
            text: item.text
            role: item.active ? Theme.type.bodyMdStrong : Theme.type.labelMd
            color: item.active ? Theme.text : Theme.textSecondary
        }
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 1
            anchors.bottomMargin: -1
            radius: Theme.radiusMd
            color: Theme.shadowNav
            visible: item.active
        }
        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusMd
            color: item.active ? Theme.surface : (item.hovered ? Theme.sidebarHover : "transparent")
        }
        FocusRing {
            targetRadius: Theme.radiusMd
            visible: item.visualFocus
        }
    }
}
