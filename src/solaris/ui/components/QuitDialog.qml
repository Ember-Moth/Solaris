pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris

// Quitting could not disconnect the tunnel in time (D41): try again, or quit and leave it as
// it may be. Esc keeps Solaris running.
AppDialog {
    id: dialog

    required property DesktopViewModel desktop

    title: qsTr("未能断开连接")
    defaultButton: retryButton

    onRejected: dialog.desktop.cancelQuit()

    AccessibleText {
        Layout.fillWidth: true
        text: dialog.desktop.quitMessage
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            text: qsTr("仍然退出")
            variant: "secondary"
            onClicked: {
                dialog.close()
                dialog.desktop.leave()
            }
        },
        AppButton {
            id: retryButton

            text: qsTr("重试")
            variant: "primary"
            onClicked: {
                dialog.close()
                dialog.desktop.retryQuit()
            }
        }
    ]
}
