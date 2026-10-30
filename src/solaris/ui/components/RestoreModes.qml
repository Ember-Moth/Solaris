pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

// Merge or replace (docs/storage.md §3), under a line on what this machine holds already: the
// choice of both restore dialogs.
ColumnLayout {
    id: modes

    required property BackupViewModel backup
    property string summary

    spacing: 0
    Accessible.role: Accessible.Grouping
    Accessible.name: summary

    UiText {
        Layout.fillWidth: true
        Layout.bottomMargin: Theme.space1
        text: modes.summary
        role: Theme.type.caption
        color: Theme.textTertiary
        wrapMode: Text.Wrap
    }

    Repeater {
        model: modes.backup.restoreModes

        AbstractButton {
            id: choice

            required property var modelData
            readonly property bool selected: modelData.value === modes.backup.restoreMode

            Layout.fillWidth: true
            topPadding: Theme.space1
            bottomPadding: Theme.space1
            checked: selected  // what screen readers hear; clicking does not toggle it
            Accessible.role: Accessible.RadioButton
            Accessible.name: modelData.label
            Accessible.description: modelData.detail
            onClicked: modes.backup.setRestoreMode(modelData.value)

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
