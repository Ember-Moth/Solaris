pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// The VPN Gate exit's settings (docs/exits.md §2): whether the plan uses it, the group it goes
// out through, which servers of VPN Gate's list it takes and how sites are shared out over
// them, and the servers in use. Each control applies at once, as the switch does (DESIGN.md
// "开关"), so the dialog only closes.
AppDialog {
    id: dialog

    required property VpnGateExit vpngate

    title: vpngate.name
    wide: true
    defaultButton: doneButton
    initialFocus: activeSwitch

    AccessibleText {
        Layout.fillWidth: true
        text: qsTr("换一个 IP 访问网站，不保护隐私：服务器由志愿者提供，他们能看到你访问的网站，并保留两周日志。请勿用于大流量下载和视频。")
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        spacing: Theme.space4

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space3

            UiText {
                Layout.fillWidth: true
                text: qsTr("启用")
            }

            AppSwitch {
                id: activeSwitch

                value: dialog.vpngate.active
                enabled: !dialog.vpngate.busy
                Accessible.name: qsTr("启用 VPN Gate 出口")
                onRequested: on => dialog.vpngate.setActive(on)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.space4

            ColumnLayout {
                spacing: 6

                UiText {
                    text: qsTr("经由")
                    role: Theme.type.caption
                    color: Theme.textTertiary
                }

                AppSelect {
                    Layout.preferredWidth: 200
                    options: dialog.vpngate.viaOptions
                    current: dialog.vpngate.via
                    displayText: dialog.vpngate.viaLabel
                    accessibleName: qsTr("经由")
                    enabled: !dialog.vpngate.busy
                    onPicked: value => dialog.vpngate.setVia(value)
                }
            }

            ColumnLayout {
                spacing: 6

                UiText {
                    text: qsTr("服务器数量")
                    role: Theme.type.caption
                    color: Theme.textTertiary
                }

                AppSelect {
                    Layout.preferredWidth: 100
                    options: dialog.vpngate.countOptions
                    current: dialog.vpngate.count
                    displayText: dialog.vpngate.countLabel
                    accessibleName: qsTr("服务器数量")
                    enabled: !dialog.vpngate.busy
                    onPicked: value => dialog.vpngate.setCount(value)
                }
            }

            Item {
                Layout.fillWidth: true
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            CheckGrid {
                Layout.fillWidth: true
                label: qsTr("国家")
                model: dialog.vpngate.countries
                enabled: !dialog.vpngate.busy
                onToggled: id => dialog.vpngate.toggleCountry(id)
            }

            AccessibleText {
                Layout.fillWidth: true
                visible: dialog.vpngate.severalCountries
                text: qsTr("一台服务器失效后，分到它的网站可能换到另一个国家的服务器上")
                role: Theme.type.caption
                color: Theme.textMuted
                wrapMode: Text.Wrap
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("分配方式")

            UiText {
                Layout.bottomMargin: Theme.space1
                text: qsTr("分配方式")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            Repeater {
                model: dialog.vpngate.modeOptions

                AbstractButton {
                    id: choice

                    required property var modelData
                    readonly property bool selected: modelData.value === dialog.vpngate.mode

                    Layout.fillWidth: true
                    topPadding: 6
                    bottomPadding: 6
                    enabled: !dialog.vpngate.busy
                    checked: selected  // what screen readers hear; clicking does not toggle it
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    Accessible.description: modelData.detail
                    onClicked: dialog.vpngate.setMode(modelData.value)

                    contentItem: RowLayout {
                        spacing: 10

                        RadioMark {
                            Layout.alignment: Qt.AlignTop
                            Layout.topMargin: 1
                            checked: choice.selected
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            UiText {
                                Layout.fillWidth: true
                                text: choice.modelData.label
                                role: Theme.type.bodySm
                                elide: Text.ElideRight
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: choice.modelData.detail
                                role: Theme.type.caption
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }

                    background: Item {
                        FocusRing {
                            targetRadius: 4
                            visible: choice.visualFocus
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            UiText {
                text: qsTr("服务器")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            Group {
                Layout.fillWidth: true

                GroupRow {
                    divider: false

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        AccessibleText {
                            Layout.fillWidth: true
                            text: dialog.vpngate.serversTitle
                            role: Theme.type.labelMd
                        }

                        AccessibleText {
                            Layout.fillWidth: true
                            text: dialog.vpngate.error !== "" ? dialog.vpngate.error : dialog.vpngate.serversDetail
                            role: Theme.type.caption
                            color: dialog.vpngate.error !== "" ? Theme.danger : Theme.textMuted
                            wrapMode: Text.Wrap
                        }
                    }

                    AppButton {
                        variant: "compact"
                        text: qsTr("立即刷新")
                        enabled: dialog.vpngate.canRefresh
                        onClicked: dialog.vpngate.refresh()
                    }
                }
            }

            // Up to ten servers: the list scrolls past five rather than growing the dialog.
            Rectangle {
                Layout.fillWidth: true
                visible: serverList.count > 0
                implicitHeight: Math.min(serverList.contentHeight, 5 * 32) + 2
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                ListView {
                    id: serverList

                    anchors.fill: parent
                    anchors.margins: 1
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.vpngate.servers
                    activeFocusOnTab: true
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("在用的服务器")
                    Keys.onUpPressed: flick(0, 800)
                    Keys.onDownPressed: flick(0, -800)

                    ScrollBar.vertical: ScrollBar {}

                    delegate: Item {
                        id: serverRow

                        required property int index
                        required property string title
                        required property string detail
                        required property string delay
                        required property string delayLevel

                        width: ListView.view.width
                        height: 32
                        Accessible.role: Accessible.ListItem
                        Accessible.name: qsTr("%1，%2，延迟 %3").arg(title).arg(detail).arg(delay)

                        Rectangle {
                            visible: serverRow.index > 0
                            width: parent.width
                            height: 1
                            color: Theme.divider
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: Theme.space3

                            UiText {
                                text: serverRow.title
                                role: Theme.type.bodySm
                                elide: Text.ElideRight
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: serverRow.detail
                                role: Theme.type.caption
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }

                            UiText {
                                text: serverRow.delay
                                role: Theme.type.bodySm
                                color: Status.latencyColor(serverRow.delayLevel)
                            }
                        }
                    }
                }
            }
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.vpngate.changeError
            role: Theme.type.caption
            color: Theme.danger
            wrapMode: Text.Wrap
        }
    }

    buttons: [
        AppButton {
            id: doneButton

            text: qsTr("完成")
            variant: "primary"
            onClicked: dialog.accept()
        }
    ]
}
