pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import MihomoDesktop

Rectangle {
    id: root

    required property ShellViewModel shell
    required property ProfilesViewModel profiles

    implicitWidth: Theme.sidebarWidth
    color: Theme.sidebar

    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.border
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.space5
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
                    text: root.profiles.hasActive ? root.profiles.activeName : qsTr("未导入配置")
                    role: Theme.type.caption
                    color: Theme.textMuted
                    elide: Text.ElideRight
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Accessible.role: Accessible.PageTabList
            Accessible.name: qsTr("页面")

            Repeater {
                model: root.shell.pages

                NavItem {
                    required property var modelData

                    Layout.fillWidth: true
                    text: modelData.label
                    iconName: modelData.icon
                    active: root.shell.currentPage === modelData.id
                    onClicked: root.shell.setPage(modelData.id)
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
