pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// Restoring from the cloud (docs/storage.md §3): which snapshot, newest first, and on a machine
// with data already whether to merge them or replace this machine's.
AppDialog {
    id: dialog

    required property BackupViewModel backup
    readonly property int rowHeight: Theme.rowDouble
    readonly property int visibleRows: 5

    title: qsTr("从云端恢复")
    wide: true
    defaultButton: restoreButton
    onRejected: backup.cancelCloudRestore()

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.space4

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.space1
            Accessible.role: Accessible.Grouping
            Accessible.name: snapshotsLabel.text

            UiText {
                id: snapshotsLabel

                Layout.fillWidth: true
                text: qsTr("备份")
                role: Theme.type.caption
                color: Theme.textTertiary
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: list.implicitHeight + 2
                radius: Theme.radiusSm
                color: Theme.surface
                border.width: 1
                border.color: Theme.border

                ListView {
                    id: list

                    anchors.fill: parent
                    anchors.margins: 1
                    implicitHeight: Math.min(contentHeight, dialog.visibleRows * dialog.rowHeight)
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.backup.cloudSnapshots

                    delegate: AbstractButton {
                        id: snapshot

                        required property var modelData
                        required property int index
                        readonly property bool selected: index === dialog.backup.cloudSnapshot

                        width: ListView.view.width
                        implicitHeight: dialog.rowHeight
                        leftPadding: Theme.space3
                        rightPadding: Theme.space3
                        checked: selected  // what screen readers hear; clicking does not toggle it
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: modelData.title
                        Accessible.description: qsTr("%1，%2").arg(modelData.detail).arg(modelData.provider)
                        onClicked: dialog.backup.setCloudSnapshot(index)
                        onActiveFocusChanged: {
                            if (activeFocus)
                                ListView.view.positionViewAtIndex(index, ListView.Contain)
                        }

                        contentItem: RowLayout {
                            spacing: Theme.space3

                            RadioMark {
                                checked: snapshot.selected
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0

                                UiText {
                                    Layout.fillWidth: true
                                    text: snapshot.modelData.title
                                    role: Theme.type.bodySm
                                    elide: Text.ElideRight
                                }

                                UiText {
                                    Layout.fillWidth: true
                                    text: snapshot.modelData.detail
                                    role: Theme.type.caption
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }

                            UiText {
                                text: snapshot.modelData.provider
                                role: Theme.type.caption
                                color: Theme.textMuted
                            }
                        }

                        background: Item {
                            Rectangle {
                                visible: snapshot.index > 0
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 1
                                color: Theme.divider
                            }

                            FocusRing {
                                targetRadius: 4
                                visible: snapshot.visualFocus
                            }
                        }
                    }
                }
            }
        }

        RestoreModes {
            Layout.fillWidth: true
            visible: dialog.backup.cloudAsksMode
            backup: dialog.backup
            summary: dialog.backup.cloudLocalSummary
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
                dialog.backup.confirmCloudRestore()
                dialog.accept()
            }
        }
    ]
}
