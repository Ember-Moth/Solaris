pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Solaris

AppDialog {
    id: dialog

    required property ProfilesViewModel profiles
    property bool subscription: false

    function save() {
        if (saveButton.enabled)
            profiles.saveProfile(nameField.text, urlField.text, autoUpdate.checked, autoUpdate.hours)
    }

    title: qsTr("编辑订阅信息")
    initialFocus: nameField
    defaultButton: saveButton
    closePolicy: profiles.editSaving ? Popup.NoAutoClose : Popup.CloseOnEscape

    onAboutToShow: {
        const details = profiles.editDetails
        subscription = details.subscription ?? false
        nameField.text = details.name ?? ""
        urlField.text = details.url ?? ""
        urlField.cursorPosition = 0
        autoUpdate.setInterval(details.autoUpdateHours ?? 0)
        nameField.selectAll()
    }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.topMargin: Theme.space3
        spacing: Theme.space3

        UiText { text: qsTr("名称"); role: Theme.type.caption; color: Theme.textTertiary }
        AppTextField {
            id: nameField
            Layout.fillWidth: true
            enabled: !dialog.profiles.editSaving
            error: dialog.profiles.editError !== "" && !dialog.subscription
            Accessible.name: qsTr("名称")
            onAccepted: dialog.save()
        }
        UiText {
            visible: dialog.subscription
            text: qsTr("订阅地址"); role: Theme.type.caption; color: Theme.textTertiary
        }
        AppTextField {
            id: urlField
            Layout.fillWidth: true
            visible: dialog.subscription
            enabled: !dialog.profiles.editSaving
            inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase
            error: dialog.profiles.editError !== ""
            Accessible.name: qsTr("订阅地址")
            onAccepted: dialog.save()
        }
        AccessibleText {
            Layout.fillWidth: true
            visible: dialog.subscription
            text: qsTr("修改地址后保留现有配置，下次更新使用新地址。")
            role: Theme.type.caption
            color: Theme.textSecondary
            wrapMode: Text.Wrap
        }
        AutoUpdateField {
            id: autoUpdate
            Layout.fillWidth: true
            visible: dialog.subscription
            defaultHours: dialog.profiles.defaultAutoUpdateHours
            enabled: !dialog.profiles.editSaving
            onAccepted: dialog.save()
        }
        AccessibleText {
            Layout.fillWidth: true
            text: dialog.profiles.editError
            visible: text !== ""
            role: Theme.type.caption
            color: Theme.danger
            wrapMode: Text.Wrap
        }
    }

    buttons: [
        AppButton {
            text: qsTr("取消")
            enabled: !dialog.profiles.editSaving
            onClicked: dialog.reject()
        },
        AppButton {
            id: saveButton
            text: dialog.profiles.editSaving ? qsTr("正在保存…") : qsTr("保存")
            variant: "primary"
            enabled: !dialog.profiles.editSaving && nameField.text.trim() !== "" && (!dialog.subscription || (urlField.text.trim() !== "" && autoUpdate.acceptableInput))
            onClicked: dialog.save()
        }
    ]
}
