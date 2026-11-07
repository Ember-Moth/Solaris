pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris

Rectangle {
    id: root

    required property ShellViewModel shell
    required property RoutingViewModel routing

    // The page's tab in the sidebar, or null while the sidebar does not list it.
    function tab(page: string): NavItem {
        for (let i = 0; i < tabs.count; ++i) {
            const item = tabs.itemAt(i) as NavItem
            if (item?.page === page)
                return item
        }
        return null
    }

    implicitWidth: Theme.sidebarWidth
    color: Theme.sidebar

    // The top of the sidebar is part of the title band.
    TitleBandArea {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Theme.titlebarHeight
    }

    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.border
    }

    ColumnLayout {
        anchors.fill: parent
        // macOS: the mark goes below the traffic lights, which the system places in the title
        // bar the safe area covers. Windows: the app's name is on the title band's centre line,
        // level with the page title and the caption buttons.
        anchors.topMargin: TitleBand.mac
            ? Math.max(Theme.sidebarTopMacos, root.SafeArea.margins.top + Theme.space6)
            : Theme.sidebarTopWindows
        anchors.bottomMargin: Theme.space5
        anchors.leftMargin: 14
        anchors.rightMargin: 15
        spacing: Theme.space6

        RowLayout {
            Layout.leftMargin: Theme.space2
            spacing: 10

            Rectangle {
                implicitWidth: 30
                implicitHeight: 30
                radius: Theme.radiusMd
                color: Theme.text

                Icon {
                    anchors.centerIn: parent
                    name: "mark"
                    size: 16
                    color: Theme.surface
                }
            }

            ColumnLayout {
                spacing: 0

                UiText {
                    text: Qt.application.name
                    role: Theme.type.bodyMdStrong
                }

                AccessibleText {
                    Layout.maximumWidth: Theme.sidebarWidth - 92
                    text: root.routing.sidebarLabel
                    role: Theme.type.caption
                    color: Theme.textMuted
                    elide: Text.ElideMiddle
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Accessible.role: Accessible.PageTabList
            Accessible.name: qsTr("页面")

            Repeater {
                id: tabs

                model: root.shell.pages
                // A tab that goes as the tunnel stops hands its focus to the shown page's tab.
                onItemRemoved: (index, item) => {
                    if (item.activeFocus)
                        root.tab(root.shell.currentPage)?.forceActiveFocus(Focus.reasonAfter(item))
                }

                NavItem {
                    required property string label
                    required page
                    required iconName

                    Layout.fillWidth: true
                    text: label
                    active: root.shell.currentPage === page
                    onClicked: root.shell.setPage(page)
                }
            }
        }

        Item {
            Layout.fillHeight: true
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: status.implicitHeight + 2 * Theme.space3
            radius: Theme.radiusMd
            color: Theme.background
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("服务状态")

            ColumnLayout {
                id: status
                anchors.fill: parent
                anchors.margins: Theme.space3
                spacing: 6

                RowLayout {
                    spacing: Theme.space2

                    StatusDot {
                        running: root.shell.serviceAvailable
                    }

                    AccessibleText {
                        text: root.shell.serviceText
                        role: Theme.type.labelSmStrong
                    }
                }

                AccessibleText {
                    Layout.fillWidth: true
                    text: root.shell.serviceDetail
                    role: Theme.type.caption
                    color: Theme.textMuted
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
