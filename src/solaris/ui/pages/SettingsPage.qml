pragma ComponentBehavior: Bound
import QtCore
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Solaris
import "../components"

ScrollView {
    id: page

    required property SettingsViewModel settings
    // Updates, diagnostics and licenses in the 关于 group.
    required property AboutViewModel about
    // TUN and the system proxy are the overview's switches, shown here as well.
    required property OverviewViewModel overview
    // The 备份与同步 group.
    required property BackupViewModel backup

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
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("TUN 协议栈")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.settings.stackError
                                    role: Theme.type.caption
                                    color: Theme.danger
                                    wrapMode: Text.Wrap
                                }
                            }

                            AppSelect {
                                options: page.settings.tunStackOptions
                                current: page.settings.tunStack
                                enabled: !page.settings.applying
                                accessibleName: qsTr("TUN 协议栈")
                                onPicked: value => page.settings.setTunStack(value)
                            }
                        }

                        // macOS only (D19): the VPN configuration takes it on the next connection.
                        GroupRow {
                            visible: page.settings.includeAllNetworksAvailable
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("包含所有网络")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: qsTr("系统默认绕开隧道的流量也进入隧道，下次连接时生效")
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    wrapMode: Text.Wrap
                                }
                            }

                            AppSwitch {
                                value: page.settings.includeAllNetworks
                                enabled: !page.settings.applying
                                Accessible.name: qsTr("包含所有网络")
                                Accessible.description: qsTr("系统默认绕开隧道的流量也进入隧道，下次连接时生效")
                                onRequested: on => page.settings.setIncludeAllNetworks(on)
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

                            AccessibleText {
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

                                AccessibleText {
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
                                Accessible.description: page.settings.portError
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

                                AccessibleText {
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
                                Accessible.description: page.settings.lanError !== "" ? page.settings.lanError : page.settings.allowLan ? qsTr("同一网络的设备可以使用混合端口") : ""
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

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.space2

                    SectionLabel {
                        text: qsTr("备份与同步")
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
                                    text: qsTr("导出到文件")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: page.backup.exportStatus
                                    role: Theme.type.caption
                                    color: page.backup.exportFailed ? Theme.danger : Theme.textMuted
                                    wrapMode: Text.Wrap
                                    Accessible.role: Accessible.StatusBar
                                }
                            }

                            AppButton {
                                id: exportButton

                                variant: "compact"
                                text: qsTr("导出…")
                                enabled: !page.backup.busy
                                Accessible.name: qsTr("导出到文件")
                                Accessible.description: page.backup.exportStatus
                                onClicked: exportDialog.show(exportButton)
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("从文件恢复")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.backup.restoreStatus
                                    role: Theme.type.caption
                                    color: page.backup.restoreFailed ? Theme.danger : Theme.textMuted
                                    wrapMode: Text.Wrap
                                    Accessible.role: Accessible.StatusBar
                                }
                            }

                            AppButton {
                                id: restoreFileButton

                                variant: "compact"
                                text: qsTr("恢复…")
                                enabled: !page.backup.busy
                                Accessible.name: qsTr("从文件恢复")
                                Accessible.description: page.backup.restoreStatus
                                onClicked: openFileDialog.open()
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

                                AccessibleText {
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
                                Accessible.description: page.settings.launchAtLoginDetail
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

                                AccessibleText {
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

                        GroupRow {
                            divider: false
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("版本")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    visible: text !== ""
                                    text: page.about.updateStatus
                                    role: Theme.type.caption
                                    color: page.about.updateFailed ? Theme.danger : Theme.textMuted
                                    wrapMode: Text.Wrap
                                    Accessible.role: Accessible.StatusBar
                                }
                            }

                            AccessibleText {
                                text: page.about.version
                                role: Theme.type.dataMd
                                color: Theme.textSecondary
                                Accessible.name: qsTr("版本") + " " + text
                            }

                            LinkButton {
                                visible: page.about.releaseUrl !== ""
                                text: qsTr("发布说明")
                                Accessible.description: page.about.releaseUrl
                                onClicked: Qt.openUrlExternally(page.about.releaseUrl)
                            }

                            AppButton {
                                id: updateButton

                                variant: "compact"
                                text: page.about.updateButton
                                enabled: page.about.updateButtonEnabled
                                Accessible.description: page.about.updateStatus
                                onClicked: page.about.runUpdateAction()
                            }
                        }

                        GroupRow {
                            rowHeight: Theme.rowSingle

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("自动检查更新")
                            }

                            AppSwitch {
                                value: page.about.autoCheck
                                Accessible.name: qsTr("自动检查更新")
                                onRequested: on => page.about.setAutoCheck(on)
                            }
                        }

                        GroupRow {
                            rowHeight: Theme.rowSingle

                            UiText {
                                Layout.fillWidth: true
                                text: qsTr("检查频率")
                                color: page.about.autoCheck ? Theme.text : Theme.textMuted
                            }

                            AppSelect {
                                options: page.about.intervalOptions
                                current: page.about.checkInterval
                                enabled: page.about.autoCheck
                                accessibleName: qsTr("检查频率")
                                onPicked: value => page.about.setCheckInterval(value)
                            }
                        }

                        Repeater {
                            model: [
                                { label: qsTr("服务"), value: page.settings.serviceVersion },
                                { label: qsTr("内核"), value: page.settings.coreVersion },
                                { label: qsTr("控制协议"), value: page.settings.protocolVersion },
                                { label: qsTr("许可证"), value: page.settings.license }
                            ]

                            GroupRow {
                                id: aboutRow

                                required property var modelData

                                rowHeight: Theme.rowSingle

                                UiText {
                                    Layout.fillWidth: true
                                    text: aboutRow.modelData.label
                                }

                                AccessibleText {
                                    text: aboutRow.modelData.value
                                    role: Theme.type.dataMd
                                    color: Theme.textSecondary
                                    Accessible.name: aboutRow.modelData.label + " " + text
                                }
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("开源许可")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: page.about.componentsSummary
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                }
                            }

                            AppButton {
                                id: licensesButton

                                variant: "compact"
                                text: qsTr("查看…")
                                Accessible.name: qsTr("查看开源许可")
                                onClicked: licensesDialog.show(licensesButton)
                            }
                        }

                        GroupRow {
                            verticalPadding: Theme.space2

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: qsTr("诊断信息")
                                }

                                AccessibleText {
                                    Layout.fillWidth: true
                                    text: page.about.diagnosticsStatus
                                    role: Theme.type.caption
                                    color: page.about.diagnosticsFailed ? Theme.danger : Theme.textMuted
                                    wrapMode: Text.Wrap
                                    Accessible.role: Accessible.StatusBar
                                }
                            }

                            AppButton {
                                variant: "compact"
                                text: qsTr("导出…")
                                enabled: !page.about.exporting
                                Accessible.name: qsTr("导出诊断信息")
                                Accessible.description: page.about.diagnosticsStatus
                                onClicked: {
                                    diagnosticsDialog.selectedFile = diagnosticsDialog.currentFolder + "/" + page.about.diagnosticsName
                                    diagnosticsDialog.open()
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

    Connections {
        target: page.about

        function onInstallRequested() {
            installDialog.show(updateButton)
        }
    }

    ConfirmDialog {
        id: installDialog

        title: page.about.installTitle
        message: page.about.installMessage
        confirmText: qsTr("打开")
        onAccepted: page.about.confirmInstall()
    }

    LicensesDialog {
        id: licensesDialog

        about: page.about
    }

    FileDialog {
        id: diagnosticsDialog

        title: qsTr("导出诊断信息")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "zip"
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [qsTr("zip 压缩文件 (*.zip)")]
        onAccepted: page.about.exportDiagnostics(selectedFile.toString())
    }

    ConfirmDialog {
        id: exportDialog

        title: qsTr("导出备份文件？")
        message: qsTr("备份文件不加密，含订阅链接和配置原文，请妥善保管。")
        confirmText: qsTr("继续")
        onAccepted: {
            exportFileDialog.selectedFile = exportFileDialog.currentFolder + "/" + page.backup.exportName
            exportFileDialog.open()
        }
    }

    FileDialog {
        id: exportFileDialog

        title: qsTr("导出到文件")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [qsTr("Solaris 备份 (*.json)")]
        onAccepted: page.backup.exportBackup(selectedFile.toString())
    }

    FileDialog {
        id: openFileDialog

        title: qsTr("从文件恢复")
        fileMode: FileDialog.OpenFile
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [qsTr("Solaris 备份 (*.json)")]
        onAccepted: page.backup.openBackup(selectedFile.toString())
    }

    RestoreDialog {
        id: restoreDialog

        backup: page.backup
    }

    Connections {
        target: page.backup

        function onRestoreRequested() {
            restoreDialog.show(restoreFileButton)
        }
    }
}
