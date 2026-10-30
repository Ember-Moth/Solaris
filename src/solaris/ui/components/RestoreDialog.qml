pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Solaris

// Restoring a backup file onto a machine that has data already (docs/storage.md §3): what the
// file holds, what this machine holds, and whether to merge them or replace this machine's.
AppDialog {
    id: dialog

    required property BackupViewModel backup

    title: qsTr("从文件恢复")
    defaultButton: restoreButton
    onRejected: backup.cancelRestore()

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space4

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.backup.snapshotTitle
                role: Theme.type.bodySm
                elide: Text.ElideRight
            }

            AccessibleText {
                Layout.fillWidth: true
                text: dialog.backup.snapshotDetail
                role: Theme.type.caption
                color: Theme.textMuted
            }
        }

        RestoreModes {
            Layout.fillWidth: true
            backup: dialog.backup
            summary: dialog.backup.localSummary
        }
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            onClicked: dialog.reject()
        },
        AppButton {
            id: restoreButton

            text: qsTr("恢复")
            variant: "primary"
            onClicked: {
                dialog.backup.confirmRestore()
                dialog.accept()
            }
        }
    ]
}
