pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

AppDialog {
    id: dialog

    required property ProfilesViewModel profiles

    function submit() {
        if (importButton.enabled)
            profiles.importNodeLinks(editor.text, nameField.text)
    }

    title: qsTr("导入节点链接")
    wide: true
    initialFocus: editor
    defaultButton: importButton
    closePolicy: profiles.importing ? Popup.NoAutoClose : Popup.CloseOnEscape
    onAboutToShow: profiles.clearImportError()
    onClosed: {
        editor.text = ""
        nameField.clear()
        profiles.clearImportError()
    }

    UiText { text: qsTr("名称"); role: Theme.type.caption; color: Theme.textTertiary }
    AppTextField {
        id: nameField
        Layout.fillWidth: true
        placeholderText: qsTr("可选")
        enabled: !dialog.profiles.importing
        Accessible.name: qsTr("名称")
        onAccepted: dialog.submit()
    }
    AccessibleText {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space3
        text: qsTr("每行一个节点链接，支持 VLESS、VMess、Shadowsocks、Trojan 等格式。")
        role: Theme.type.bodySm
        color: Theme.textSecondary
        wrapMode: Text.Wrap
    }
    CodeEditor {
        id: editor
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredHeight: Theme.editorMinHeight
        placeholderText: "vless://…"
        readOnly: dialog.profiles.importing
        error: dialog.profiles.importError !== ""
        Accessible.name: qsTr("节点链接")
        onSubmitted: dialog.submit()
    }
    AccessibleText {
        Layout.fillWidth: true
        visible: text !== ""
        text: dialog.profiles.importError
        role: Theme.type.caption
        color: Theme.danger
        wrapMode: Text.Wrap
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            enabled: !dialog.profiles.importing
            onClicked: dialog.reject()
        },
        AppButton {
            id: importButton
            text: dialog.profiles.importing ? qsTr("正在导入…") : qsTr("导入")
            variant: "primary"
            enabled: !dialog.profiles.importing && editor.text.trim() !== ""
            onClicked: dialog.submit()
        }
    ]
}
