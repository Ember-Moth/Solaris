pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// macOS's approval is a setup step, not a failed connection. The guide stays open
// during an explicit retry; a successful connection or a different error closes it.
AppDialog {
    id: dialog

    required property OverviewViewModel overview
    readonly property bool activationPending: overview.extensionSetup === "pending"

    title: activationPending ? qsTr("网络扩展尚未就绪") : qsTr("允许 Solaris 的网络扩展")
    wide: !activationPending
    defaultButton: continueButton
    closePolicy: overview.busy ? Popup.NoAutoClose : Popup.CloseOnEscape

    AccessibleText {
        Layout.fillWidth: true
        text: dialog.activationPending
            ? qsTr("macOS 尚未完成网络扩展的启用。请按系统提示完成设置，然后重试连接。")
            : qsTr("连接前，需要先在 macOS 中允许 Solaris 的网络扩展。")
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        visible: !dialog.activationPending
        spacing: Theme.space4

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.space2

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("1. 打开「登录项与扩展」")
                role: Theme.type.bodyMdStrong
                wrapMode: Text.Wrap
            }

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("系统设置 › 通用 › 登录项与扩展")
                role: Theme.type.bodySm
                color: Theme.textSecondary
                wrapMode: Text.Wrap
            }

            AppButton {
                text: qsTr("打开系统设置")
                enabled: !dialog.overview.busy && !dialog.overview.extensionSettingsOpening
                onClicked: dialog.overview.openExtensionSettings()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.space2

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("2. 允许 Solaris")
                role: Theme.type.bodyMdStrong
                wrapMode: Text.Wrap
            }

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("在「网络扩展」右侧点击信息按钮，打开 Solaris，并按系统提示确认。")
                role: Theme.type.bodySm
                color: Theme.textSecondary
                wrapMode: Text.Wrap
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.space2

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("3. 返回这里继续连接")
                role: Theme.type.bodyMdStrong
                wrapMode: Text.Wrap
            }

            AccessibleText {
                Layout.fillWidth: true
                text: qsTr("点击「继续连接」。如果 macOS 询问是否允许添加 VPN 配置，请选择「允许」。")
                role: Theme.type.bodySm
                color: Theme.textSecondary
                wrapMode: Text.Wrap
            }
        }
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: !dialog.activationPending && text !== ""
        text: dialog.overview.extensionSettingsError
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            enabled: !dialog.overview.busy
            onClicked: dialog.reject()
        },
        AppButton {
            id: continueButton

            text: dialog.overview.busy ? qsTr("正在连接…")
                : dialog.activationPending ? qsTr("重试连接") : qsTr("继续连接")
            variant: "primary"
            enabled: !dialog.overview.busy && !dialog.overview.extensionSettingsOpening
            onClicked: dialog.overview.setConnected(true)
        }
    ]
}
