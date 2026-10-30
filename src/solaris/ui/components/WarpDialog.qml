pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// The WARP exit's settings (docs/exits.md §2): whether the plan uses it, the group it goes
// out through, this machine's protocol and its account. Each control applies at once, as
// the switch does (DESIGN.md "开关"), so the dialog only closes.
AppDialog {
    id: dialog

    required property WarpExit warp

    title: warp.name
    defaultButton: doneButton
    initialFocus: activeSwitch

    Connections {
        target: dialog.warp

        function onRemovalRequested() {
            forgetDialog.showFor(accountMenu.trigger, accountMenu.triggerFocusReason)
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        text: qsTr("Cloudflare 能看到访问的网站，代理节点看不到")
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

                value: dialog.warp.active
                enabled: !dialog.warp.busy
                Accessible.name: qsTr("启用 WARP 出口")
                onRequested: on => dialog.warp.setActive(on)
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            UiText {
                text: qsTr("经由")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            AppSelect {
                Layout.preferredWidth: 220
                options: dialog.warp.viaOptions
                current: dialog.warp.via
                displayText: dialog.warp.viaLabel
                accessibleName: qsTr("经由")
                enabled: !dialog.warp.busy
                onPicked: value => dialog.warp.setVia(value)
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("协议")

            UiText {
                Layout.bottomMargin: Theme.space1
                text: qsTr("协议")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            Repeater {
                model: dialog.warp.protocolOptions

                AbstractButton {
                    id: choice

                    required property var modelData
                    readonly property bool selected: modelData.value === dialog.warp.protocol

                    Layout.fillWidth: true
                    topPadding: 6
                    bottomPadding: 6
                    enabled: !dialog.warp.busy
                    checked: selected  // what screen readers hear; clicking does not toggle it
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    Accessible.description: modelData.detail
                    onClicked: dialog.warp.setProtocol(modelData.value)

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
                text: qsTr("账户")
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
                            text: dialog.warp.accountTitle
                            role: Theme.type.labelMd
                        }

                        AccessibleText {
                            Layout.fillWidth: true
                            text: dialog.warp.error !== "" ? dialog.warp.error : dialog.warp.accountDetail
                            role: Theme.type.caption
                            color: dialog.warp.error !== "" ? Theme.danger : Theme.textMuted
                            wrapMode: Text.Wrap
                        }
                    }

                    AppButton {
                        variant: "compact"
                        text: dialog.warp.registerLabel
                        enabled: dialog.warp.canRegister
                        onClicked: dialog.warp.register()
                    }

                    IconButton {
                        id: accountMore

                        visible: dialog.warp.hasAccount
                        iconName: "more"
                        accessibleName: qsTr("账户的更多操作")
                        onClicked: accountMenu.openBelow(accountMore)
                    }
                }
            }
        }

        AccessibleText {
            Layout.fillWidth: true
            visible: text !== ""
            text: dialog.warp.changeError
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

    AppMenu {
        id: accountMenu

        AppMenuItem {
            text: qsTr("删除账户…")
            destructive: true
            onTriggered: dialog.warp.requestForget()
        }
    }

    ConfirmDialog {
        id: forgetDialog

        title: qsTr("删除 WARP 账户？")
        message: qsTr("只删除这台设备保存的账户。指向 WARP 出口的规则会拒绝连接，直到重新注册。")
        confirmText: qsTr("删除")
        destructive: true
        onAccepted: dialog.warp.confirmForget()
    }
}
