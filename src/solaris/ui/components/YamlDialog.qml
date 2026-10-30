import QtCore
import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import Solaris

// YAML the core wrote, read only (docs/routing.md §4.7, §3.3): the plan exported to share,
// which can be copied or saved, or the configuration the core would run, which can be copied.
AppDialog {
    id: dialog

    required property PlanTransfer transfer

    title: transfer.yamlTitle
    wide: true
    defaultButton: copyButton
    initialFocus: yamlView

    AccessibleText {
        Layout.fillWidth: true
        text: dialog.transfer.yamlNote
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }

    CodeEditor {
        id: yamlView

        Layout.fillWidth: true
        Layout.topMargin: Theme.space2
        Layout.preferredHeight: 360
        implicitHeight: 360
        readOnly: true
        text: dialog.transfer.yaml
        Accessible.name: dialog.title
    }

    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: dialog.transfer.notice
        role: Theme.type.caption
        color: Theme.textMuted
        elide: Text.ElideMiddle
    }

    buttons: [
        AppButton {
            text: qsTr("关闭")
            onClicked: dialog.reject()
        },
        AppButton {
            visible: dialog.transfer.sharing
            text: qsTr("另存为…")
            onClicked: {
                saveDialog.selectedFile = saveDialog.currentFolder + "/" + dialog.transfer.exportName
                saveDialog.open()
            }
        },
        AppButton {
            id: copyButton

            text: qsTr("复制")
            variant: "primary"
            onClicked: dialog.transfer.copyYaml()
        }
    ]

    FileDialog {
        id: saveDialog

        title: qsTr("导出方案")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "yaml"
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [qsTr("YAML 配置 (*.yaml *.yml)")]
        onAccepted: dialog.transfer.saveYaml(selectedFile.toString())
    }
}
