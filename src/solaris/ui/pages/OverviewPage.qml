pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris
import "../components"

ScrollView {
    id: page

    required property OverviewViewModel overview
    required property ProfilesViewModel profiles
    required property RoutingViewModel routing
    required property ShellViewModel shell
    readonly property alias connectionControl: connectionSwitch

    contentWidth: availableWidth
    clip: true

    ScrollToFocus {
        flickable: page.contentItem as Flickable
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: Theme.space5

        PageHeader {
            Layout.fillWidth: true
            Layout.topMargin: Theme.titlebarPageTop
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            title: page.overview.statusTitle
            detail: page.overview.statusDetail

            // An administrator may stop another user's tunnel and use it instead (D5b).
            AppButton {
                id: takeOverButton

                visible: page.overview.canTakeOver
                enabled: !page.overview.busy
                text: qsTr("接管")
                onClicked: takeOverDialog.show(takeOverButton)
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
                    text: qsTr("出站")
                }

                Group {
                    Layout.fillWidth: true

                    // The only connection control (D26, DESIGN.md "连接开关"); what connecting
                    // takes over is the settings page's 接管方式.
                    GroupRow {
                        divider: false
                        verticalPadding: Theme.space2

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("连接")
                            }

                            AccessibleText {
                                Layout.fillWidth: true
                                visible: text !== ""
                                text: page.overview.connectionDetail
                                role: Theme.type.caption
                                color: page.overview.connectionFailed ? Theme.danger
                                    : page.overview.extensionSetup !== "" ? Theme.warning : Theme.textMuted
                                wrapMode: Text.Wrap
                            }
                        }

                        AppButton {
                            visible: page.overview.extensionSetup !== ""
                            enabled: !page.overview.busy
                            text: qsTr("安装指引…")
                            variant: "compact"
                            onClicked: page.overview.showExtensionSetup()
                        }

                        AppSwitch {
                            id: connectionSwitch

                            value: page.overview.connectionOn
                            // Another user's tunnel is theirs to disconnect (docs/architecture.md §5.4).
                            enabled: !page.overview.busy && !page.overview.connectionChanging && !page.overview.ownedByOtherUser
                            Accessible.name: qsTr("连接")
                            Accessible.description: page.overview.connectionDetail
                            onRequested: on => page.overview.setConnected(on)
                        }
                    }

                    GroupRow {
                        UiText {
                            Layout.fillWidth: true
                            text: qsTr("模式")
                        }

                        SegmentedControl {
                            options: page.overview.modeOptions
                            current: page.overview.mode
                            accessibleName: qsTr("模式")
                            enabled: !page.overview.busy
                            onPicked: value => page.overview.setMode(value)
                        }
                    }

                    // What routes the traffic (docs/routing.md §4.6): a plan, or a profile's own
                    // rules; the routing page is where it changes.
                    GroupRow {
                        UiText {
                            Layout.fillWidth: true
                            text: qsTr("当前使用")
                        }

                        LinkButton {
                            visible: page.routing.mode === "profile"
                            text: qsTr("改用我的方案")
                            onClicked: {
                                page.routing.setMode("plan")
                                page.shell.setPage("routing")
                            }
                        }

                        Button {
                            id: usingButton

                            implicitHeight: Theme.controlSm
                            leftPadding: 10
                            rightPadding: Theme.space2
                            topPadding: 0
                            bottomPadding: 0
                            Accessible.name: qsTr("当前使用 %1，前往分流页").arg(page.routing.usingLabel)
                            onClicked: page.shell.setPage("routing")

                            contentItem: RowLayout {
                                spacing: 10

                                UiText {
                                    text: page.routing.usingLabel
                                    role: Theme.type.labelMd
                                }

                                Icon {
                                    name: "chevron"
                                    size: 14
                                }
                            }

                            background: Rectangle {
                                radius: Theme.radiusSm
                                color: usingButton.down ? Theme.surfaceSelected : usingButton.hovered ? Theme.surfaceSubtle : Theme.surface
                                border.width: 1
                                border.color: Theme.borderButton

                                FocusRing {
                                    targetRadius: Theme.radiusSm
                                    visible: usingButton.visualFocus
                                }
                            }
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
                            Accessible.name: qsTr("当前节点 %1，前往代理页切换").arg(page.overview.nodeName)
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
                }
            }

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space2

                SectionLabel {
                    text: qsTr("流量")
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
                                Accessible.ignored: true
                            }

                            UiText {
                                Layout.leftMargin: -Theme.space3
                                text: page.overview.downRate
                                role: Theme.type.dataMd
                                Accessible.name: qsTr("下载 %1").arg(text)
                            }

                            UiText {
                                text: "↑"
                                role: Theme.type.dataMd
                                color: Theme.chartUpload
                                Accessible.ignored: true
                            }

                            UiText {
                                Layout.leftMargin: -Theme.space3
                                text: page.overview.upRate
                                role: Theme.type.dataMd
                                Accessible.name: qsTr("上传 %1").arg(text)
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            UiText {
                                text: qsTr("本次 ↓ %1 ↑ %2").arg(page.overview.downTotal).arg(page.overview.upTotal)
                                Accessible.name: qsTr("本次下载 %1，上传 %2").arg(page.overview.downTotal).arg(page.overview.upTotal)
                                role: Theme.type.dataSm
                                color: Theme.textMuted
                            }
                        }

                        TrafficChart {
                            Layout.fillWidth: true
                            down: page.overview.downPoints
                            up: page.overview.upPoints
                            Accessible.description: qsTr("下载 %1，上传 %2").arg(page.overview.downRate).arg(page.overview.upRate)
                        }

                        RowLayout {
                            Layout.fillWidth: true

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("%n 秒前", "", 40)
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }

                            UiText {
                                text: qsTr("现在")
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
                        text: qsTr("订阅")
                    }

                    LinkButton {
                        text: qsTr("管理")
                        onClicked: page.shell.setPage("profiles")
                    }
                }

                // The subscriptions and their quotas (docs/routing.md §4.6); what routes the
                // traffic is in the outbound group.
                Group {
                    Layout.fillWidth: true

                    Repeater {
                        id: subscriptionRepeater

                        model: page.profiles.model

                        GroupRow {
                            id: subscription

                            required property int index
                            required property string name
                            required property string warning
                            required property string usage
                            required property string expiry
                            required property bool hasQuota
                            required property real usedFraction

                            divider: index > 0
                            rowHeight: 0
                            verticalPadding: Theme.space3

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.space3

                                    AccessibleText {
                                        Layout.preferredWidth: Math.ceil(implicitWidth)
                                        Layout.maximumWidth: Math.ceil(implicitWidth)
                                        Layout.fillWidth: true
                                        text: subscription.name
                                        role: Theme.type.caption
                                        color: Theme.textSecondary
                                        elide: Text.ElideRight
                                    }

                                    // A failed update, in the warning colour after the name.
                                    AccessibleText {
                                        Layout.leftMargin: -Theme.space3
                                        visible: subscription.warning !== ""
                                        text: qsTr(" · %1").arg(subscription.warning)
                                        role: Theme.type.caption
                                        color: Theme.warning
                                    }

                                    Item {
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 0
                                    }

                                    AccessibleText {
                                        text: subscription.expiry !== "—" ? qsTr("%1 · %2").arg(subscription.usage).arg(subscription.expiry) : subscription.usage
                                        role: Theme.type.caption
                                        color: Theme.textTertiary
                                        font.features: ({
                                                "tnum": 1
                                            })
                                    }
                                }

                                UsageBar {
                                    Layout.fillWidth: true
                                    visible: subscription.hasQuota
                                    fraction: subscription.usedFraction
                                }
                            }
                        }
                    }

                    GroupRow {
                        visible: subscriptionRepeater.count === 0
                        divider: false

                        AccessibleText {
                            Layout.fillWidth: true
                            text: qsTr("尚未导入订阅")
                            color: Theme.textMuted
                        }

                        LinkButton {
                            text: qsTr("导入")
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
                        text: qsTr("最近连接")
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

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: recentRow.modelData.host
                                    role: Theme.type.bodySm
                                    elide: Text.ElideRight
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: recentRow.modelData.route
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }

                            AccessibleText {
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

                        AccessibleText {
                            Layout.fillWidth: true
                            text: page.overview.connected ? qsTr("没有活动连接") : qsTr("未连接")
                            role: Theme.type.bodySm
                            color: Theme.textMuted
                        }
                    }
                }
            }
        }
    }

    ConfirmDialog {
        id: takeOverDialog

        title: qsTr("接管其他用户的隧道？")
        message: qsTr("另一位用户的连接会断开，隧道改由你使用。需要管理员权限。")
        confirmText: qsTr("接管")
        destructive: true
        onAccepted: page.overview.takeOver()
    }
}
