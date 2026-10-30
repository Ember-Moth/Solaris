pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
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

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Accessible.role: Accessible.Grouping
            Accessible.name: dialog.backup.localSummary

            UiText {
                Layout.fillWidth: true
                Layout.bottomMargin: Theme.space1
                text: dialog.backup.localSummary
                role: Theme.type.caption
                color: Theme.textTertiary
                wrapMode: Text.Wrap
            }

            Repeater {
                model: dialog.backup.restoreModes

                AbstractButton {
                    id: choice

                    required property var modelData
                    readonly property bool selected: modelData.value === dialog.backup.restoreMode

                    Layout.fillWidth: true
                    topPadding: Theme.space1
                    bottomPadding: Theme.space1
                    checked: selected  // what screen readers hear; clicking does not toggle it
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: modelData.label
                    Accessible.description: modelData.detail
                    onClicked: dialog.backup.setRestoreMode(modelData.value)

                    contentItem: RowLayout {
                        spacing: 10

                        RadioMark {
                            Layout.alignment: Qt.AlignTop
                            checked: choice.selected
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            UiText {
                                Layout.fillWidth: true
                                text: choice.modelData.label
                                role: Theme.type.bodySm
                            }

                            UiText {
                                Layout.fillWidth: true
                                text: choice.modelData.detail
                                role: Theme.type.caption
                                color: Theme.textMuted
                                wrapMode: Text.Wrap
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
