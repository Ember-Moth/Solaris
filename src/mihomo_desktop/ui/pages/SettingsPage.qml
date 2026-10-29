pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import MihomoDesktop
import "../components"

ScrollView {
    id: page

    required property SettingsViewModel settings
    // TUN and the system proxy are the overview's switches, shown here as well.
    required property OverviewViewModel overview

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
            Layout.topMargin: Theme.pageTop
            Layout.leftMargin: Theme.pageX
            Layout.rightMargin: Theme.pageX
            title: qsTr("设置")
            detail: page.settings.error
            detailColor: Theme.danger
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
                spacing: Theme.space5

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    SectionLabel {
                        text: qsTr("接管方式")
                    }

                    Group {
                        Layout.fillWidth: true

                        GroupRow {
                            divider: false

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("TUN 模式")
                            }

                            AppSwitch {
                                value: page.overview.tunEnabled
                                enabled: !page.overview.busy
                                Accessible.name: qsTr("TUN 模式")
                                onRequested: on => page.overview.setTun(on)
                            }
                        }

                        GroupRow {
                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("系统代理")
                            }

                            AppSwitch {
                                value: page.overview.systemProxyEnabled
                                enabled: !page.overview.busy
                                Accessible.name: qsTr("系统代理")
                                onRequested: on => page.overview.setSystemProxy(on)
                            }
                        }

                        GroupRow {
                            visible: page.overview.switchError !== ""
                            rowHeight: Theme.rowSingle
                            verticalPadding: Theme.space2

                            UiText {
                                Layout.fillWidth: true
                                text: page.overview.switchError
                                role: Theme.type.caption
                                color: Theme.danger
                                wrapMode: Text.Wrap
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("混合端口")
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.settings.portError
                                    role: Theme.type.caption
                                    color: Theme.danger
                                    wrapMode: Text.Wrap
                                }
                            }

                            AppTextField {
                                id: portField

                                implicitWidth: 88
                                implicitHeight: Theme.controlSm
                                leftPadding: Theme.space2
                                rightPadding: Theme.space2
                                font.family: Fonts.resolve(Theme.type.dataMd.fontFamily)
                                font.pixelSize: Theme.type.dataMd.fontSize
                                text: page.settings.mixedPort.toString()
                                error: page.settings.portError !== ""
                                enabled: !page.settings.applying
                                inputMethodHints: Qt.ImhDigitsOnly
                                validator: RegularExpressionValidator {
                                    regularExpression: /[0-9]{0,5}/
                                }
                                Accessible.name: qsTr("混合端口")
                                onEditingFinished: page.settings.setMixedPort(text)
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("允许局域网连接")
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.settings.lanError !== "" ? page.settings.lanError : page.settings.allowLan ? qsTr("同一网络的设备可以使用混合端口") : ""
                                    role: Theme.type.caption
                                    color: page.settings.lanError !== "" ? Theme.danger : Theme.warning
                                    wrapMode: Text.Wrap
                                }
                            }

                            AppSwitch {
                                value: page.settings.allowLan
                                enabled: !page.settings.applying
                                Accessible.name: qsTr("允许局域网连接")
                                onRequested: on => page.settings.setAllowLan(on)
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    SectionLabel {
                        text: qsTr("界面")
                    }

                    Group {
                        Layout.fillWidth: true

                        GroupRow {
                            divider: false

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("语言")
                            }

                            AppSelect {
                                options: page.settings.languageOptions
                                current: page.settings.language
                                accessibleName: qsTr("语言")
                                onPicked: value => page.settings.setLanguage(value)
                            }
                        }

                        GroupRow {
                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("外观")
                            }

                            SegmentedControl {
                                options: page.settings.appearanceOptions
                                current: page.settings.appearance
                                accessibleName: qsTr("外观")
                                onPicked: value => page.settings.setAppearance(value)
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.preferredWidth: grid.columnWidth
                Layout.alignment: Qt.AlignTop
                spacing: Theme.space5

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    SectionLabel {
                        text: qsTr("启动")
                    }

                    Group {
                        Layout.fillWidth: true

                        GroupRow {
                            divider: false
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("登录时打开")
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.settings.launchAtLoginDetail
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    wrapMode: Text.Wrap
                                }
                            }

                            LinkButton {
                                visible: page.settings.launchAtLoginNeedsApproval
                                text: qsTr("打开系统设置")
                                onClicked: page.settings.openLoginItemSettings()
                            }

                            AppSwitch {
                                value: page.settings.launchAtLogin
                                enabled: page.settings.launchAtLoginAvailable
                                Accessible.name: qsTr("登录时打开")
                                onRequested: on => page.settings.setLaunchAtLogin(on)
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("关闭窗口时")
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("隧道继续运行")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }
                            }

                            AppSelect {
                                options: page.settings.closeActionOptions
                                current: page.settings.closeAction
                                accessibleName: qsTr("关闭窗口时")
                                onPicked: value => page.settings.setCloseAction(value)
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    SectionLabel {
                        text: qsTr("关于")
                    }

                    Group {
                        Layout.fillWidth: true

                        Repeater {
                            model: [
                                { label: qsTr("版本"), value: page.settings.version },
                                { label: qsTr("服务"), value: page.settings.serviceVersion },
                                { label: qsTr("内核"), value: page.settings.coreVersion },
                                { label: qsTr("控制协议"), value: page.settings.protocolVersion },
                                { label: qsTr("许可证"), value: page.settings.license }
                            ]

                            GroupRow {
                                id: aboutRow

                                required property var modelData
                                required property int index

                                divider: index > 0
                                rowHeight: Theme.rowSingle

                                UiText {
                                    Layout.fillWidth: true
                                    text: aboutRow.modelData.label
                                }

                                UiText {
                                    text: aboutRow.modelData.value
                                    role: Theme.type.dataMd
                                    color: Theme.textSecondary
                                }
                            }
                        }

                        GroupRow {
                            rowHeight: Theme.rowSingle

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("项目主页")
                            }

                            LinkButton {
                                text: page.settings.projectLabel
                                Accessible.description: page.settings.projectUrl
                                onClicked: Qt.openUrlExternally(page.settings.projectUrl)
                            }
                        }
                    }
                }
            }
        }
    }
}
