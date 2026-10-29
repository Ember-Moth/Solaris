pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import MihomoDesktop
import "../components"

ScrollView {
    id: page

    required property OverviewViewModel overview
    required property ShellViewModel shell

    contentWidth: availableWidth
    clip: true

    ColumnLayout {
        width: page.availableWidth
        spacing: Theme.space5

        PageHeader {
            Layout.fillWidth: true
            Layout.topMargin: Theme.pageTop
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            title: page.overview.statusTitle
            detail: page.overview.statusDetail

            AppButton {
                text: page.overview.connectLabel
                variant: page.overview.connected ? "secondary" : "primary"
                enabled: !page.overview.busy
                onClicked: page.overview.toggleConnection()
            }
        }

        GridLayout {
            id: grid

            readonly property real columnWidth: (width - (columns - 1) * columnSpacing) / columns

            Layout.fillWidth: true
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            Layout.bottomMargin: Theme.pageBottom
            columns: width >= 2 * Theme.groupColumnMin + Theme.space6 ? 2 : 1
            columnSpacing: Theme.space6
            rowSpacing: Theme.space5

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                SectionLabel {
                    text: "出站"
                }

                Group {
                    Layout.fillWidth: true

                    GroupRow {
                        divider: false

                        UiText {
                            Layout.fillWidth: true
                            text: "模式"
                        }

                        SegmentedControl {
                            options: page.overview.modeOptions
                            current: page.overview.mode
                            accessibleName: "模式"
                            enabled: !page.overview.busy
                            onPicked: value => page.overview.setMode(value)
                        }
                    }

                    GroupRow {
                        UiText {
                            Layout.fillWidth: true
                            text: page.overview.nodeGroup
                        }

                        Button {
                            id: nodeButton

                            implicitHeight: Theme.controlSm
                            leftPadding: 10
                            rightPadding: Theme.space2
                            topPadding: 0
                            bottomPadding: 0
                            Accessible.name: "当前节点 " + page.overview.nodeName + "，前往代理页切换"
                            onClicked: page.shell.setPage("proxies")

                            contentItem: RowLayout {
                                spacing: 10

                                UiText {
                                    text: page.overview.nodeName
                                    role: Theme.type.labelMd
                                }

                                UiText {
                                    visible: text !== ""
                                    text: page.overview.nodeDelay
                                    role: Theme.type.dataSm
                                    color: Status.latencyColor(page.overview.nodeDelayLevel)
                                }

                                Icon {
                                    name: "chevron"
                                    size: 14
                                }
                            }

                            background: Rectangle {
                                radius: Theme.radiusSm
                                color: nodeButton.down ? Theme.surfaceSelected : nodeButton.hovered ? Theme.surfaceSubtle : Theme.surface
                                border.width: 1
                                border.color: Theme.borderButton

                                FocusRing {
                                    targetRadius: Theme.radiusSm
                                    visible: nodeButton.visualFocus
                                }
                            }
                        }
                    }

                    GroupRow {
                        UiText {
                            Layout.fillWidth: true
                            text: "TUN 模式"
                        }

                        AppSwitch {
                            value: page.overview.tunEnabled
                            enabled: !page.overview.busy
                            Accessible.name: "TUN 模式"
                            onRequested: on => page.overview.setTun(on)
                        }
                    }

                    GroupRow {
                        UiText {
                            Layout.fillWidth: true
                            text: "系统代理"
                        }

                        AppSwitch {
                            value: page.overview.systemProxyEnabled
                            enabled: !page.overview.busy
                            Accessible.name: "系统代理"
                            onRequested: on => page.overview.setSystemProxy(on)
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                SectionLabel {
                    text: "流量"
                }

                Group {
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.margins: Theme.space4
                        Layout.topMargin: 14
                        Layout.bottomMargin: 14
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.space4

                            UiText {
                                text: "↓"
                                role: Theme.type.dataMd
                                color: Theme.primary
                            }

                            UiText {
                                Layout.leftMargin: -Theme.space3
                                text: page.overview.downRate
                                role: Theme.type.dataMd
                                Accessible.name: "下载 " + text
                            }

                            UiText {
                                text: "↑"
                                role: Theme.type.dataMd
                                color: Theme.chartUpload
                            }

                            UiText {
                                Layout.leftMargin: -Theme.space3
                                text: page.overview.upRate
                                role: Theme.type.dataMd
                                Accessible.name: "上传 " + text
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            UiText {
                                text: "本次 ↓ " + page.overview.downTotal + " ↑ " + page.overview.upTotal
                                role: Theme.type.dataSm
                                color: Theme.textMuted
                            }
                        }

                        TrafficChart {
                            Layout.fillWidth: true
                            down: page.overview.downPoints
                            up: page.overview.upPoints
                        }

                        RowLayout {
                            Layout.fillWidth: true

                            UiText {
                                Layout.fillWidth: true
                                text: "40 秒前"
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }

                            UiText {
                                text: "现在"
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                RowLayout {
                    Layout.fillWidth: true

                    SectionLabel {
                        Layout.fillWidth: true
                        text: "配置"
                    }

                    LinkButton {
                        text: "管理"
                        onClicked: page.shell.setPage("profiles")
                    }
                }

                Group {
                    Layout.fillWidth: true

                    GroupRow {
                        visible: page.overview.hasProfile
                        divider: false

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            UiText {
                                text: page.overview.profileName
                                role: Theme.type.labelMd
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: page.overview.profileDetail
                                role: Theme.type.caption
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }

                        AppButton {
                            variant: "compact"
                            text: "更新"
                            enabled: false
                        }
                    }

                    GroupRow {
                        visible: page.overview.hasProfile && page.overview.profileHasQuota
                        verticalPadding: Theme.space3

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            RowLayout {
                                Layout.fillWidth: true

                                UiText {
                                    Layout.fillWidth: true
                                    text: page.overview.profileUsage
                                    role: Theme.type.dataSm
                                    color: Theme.textTertiary
                                }

                                UiText {
                                    text: page.overview.profileExpiry
                                    role: Theme.type.caption
                                    color: Theme.textTertiary
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 4
                                radius: 2
                                color: Theme.divider
                                Accessible.role: Accessible.ProgressBar
                                Accessible.name: "已用流量"

                                Rectangle {
                                    width: parent.width * page.overview.profileUsedFraction
                                    height: parent.height
                                    radius: 2
                                    color: Theme.textTertiary
                                }
                            }
                        }
                    }

                    GroupRow {
                        visible: !page.overview.hasProfile
                        divider: false

                        UiText {
                            Layout.fillWidth: true
                            text: "尚未导入配置"
                            color: Theme.textMuted
                        }

                        LinkButton {
                            text: "导入"
                            onClicked: page.shell.setPage("profiles")
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                RowLayout {
                    Layout.fillWidth: true

                    SectionLabel {
                        Layout.fillWidth: true
                        text: "最近连接"
                    }

                    LinkButton {
                        text: page.overview.connectionsLink
                        onClicked: page.shell.setPage("connections")
                    }
                }

                Group {
                    Layout.fillWidth: true

                    Repeater {
                        model: page.overview.recent

                        GroupRow {
                            id: recentRow

                            required property var modelData
                            required property int index

                            divider: index > 0
                            rowHeight: Theme.rowSingle
                            verticalPadding: 6

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: recentRow.modelData.host
                                    role: Theme.type.bodySm
                                    elide: Text.ElideRight
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    text: recentRow.modelData.route
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }

                            UiText {
                                text: recentRow.modelData.rate
                                role: Theme.type.dataSm
                                color: Theme.textTertiary
                            }
                        }
                    }

                    GroupRow {
                        visible: page.overview.recent.length === 0
                        divider: false
                        rowHeight: Theme.rowSingle

                        UiText {
                            Layout.fillWidth: true
                            text: page.overview.connected ? "没有活动连接" : "未连接"
                            role: Theme.type.bodySm
                            color: Theme.textMuted
                        }
                    }
                }
            }
        }
    }
}
